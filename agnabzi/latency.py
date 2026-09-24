"""Continuous latency, jitter and packet-loss measurement.

Two targets are probed side by side: the default gateway (the modem or router)
and a public host. Comparing them tells a local Wi-Fi problem apart from a
problem on the provider's side.
"""

from __future__ import annotations

import logging
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol

from agnabzi.netinfo import default_gateway, resolve_ipv4
from agnabzi.system import IS_WINDOWS

log = logging.getLogger(__name__)

STATS_WINDOW = 30
HISTORY_LENGTH = 150
MIN_SAMPLES_FOR_QUALITY = 10
# A single dropped probe is normal; two or more in the window are not.
LOSS_THRESHOLD = 0.065


class Pinger(Protocol):
    def ping(self, ip: str, timeout_ms: int = 1000) -> float | None: ...


class IcmpApiPinger:
    def __init__(self) -> None:
        from agnabzi.windows.api import IcmpPinger

        self._icmp = IcmpPinger()

    def ping(self, ip: str, timeout_ms: int = 1000) -> float | None:
        result = self._icmp.echo(ip, timeout_ms)
        return result.rtt_ms if result.status == "ok" else None


class SubprocessPinger:
    _TIME = re.compile(r"time[=<]\s*([\d.]+)")

    def ping(self, ip: str, timeout_ms: int = 1000) -> float | None:
        seconds = str(max(1, round(timeout_ms / 1000)))
        timeout_flag = ["-t", seconds] if sys.platform == "darwin" else ["-W", seconds]
        try:
            output = subprocess.run(
                ["ping", "-n", "-c", "1", *timeout_flag, ip],
                capture_output=True, text=True, timeout=timeout_ms / 1000 + 2, check=False,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        match = self._TIME.search(output)
        return float(match.group(1)) if match else None


class TcpPinger:
    """Fallback that times a TCP handshake; a refused connection still proves reachability."""

    PORTS = (443, 53, 80)

    def ping(self, ip: str, timeout_ms: int = 1000) -> float | None:
        for port in self.PORTS:
            started = time.perf_counter()
            try:
                with socket.create_connection((ip, port), timeout=timeout_ms / 1000):
                    pass
            except ConnectionRefusedError:
                pass
            except OSError:
                continue
            return (time.perf_counter() - started) * 1000
        return None


def create_pinger() -> Pinger:
    if IS_WINDOWS:
        try:
            return IcmpApiPinger()
        except OSError:
            log.warning("ICMP API unavailable, falling back to TCP probes")
            return TcpPinger()
    if shutil.which("ping"):
        candidate = SubprocessPinger()
        if candidate.ping("127.0.0.1") is not None:
            return candidate
    return TcpPinger()


@dataclass(frozen=True)
class TargetStats:
    samples: int
    replies_total: int
    last: float | None
    avg: float | None
    minimum: float | None
    maximum: float | None
    jitter: float | None
    loss: float
    recent_loss: float

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "samples": self.samples, "last": self.last, "avg": self.avg, "min": self.minimum,
            "max": self.maximum, "jitter": self.jitter, "loss": self.loss,
        }


def compute_stats(values: list[float | None], replies_total: int) -> TargetStats:
    window = values[-STATS_WINDOW:]
    replies = [v for v in window if v is not None]
    recent = window[-3:]
    jitter = None
    if len(replies) >= 2:
        jitter = statistics.fmean(abs(b - a) for a, b in zip(replies, replies[1:]))
    return TargetStats(
        samples=len(window),
        replies_total=replies_total,
        last=window[-1] if window else None,
        avg=statistics.fmean(replies) if replies else None,
        minimum=min(replies) if replies else None,
        maximum=max(replies) if replies else None,
        jitter=jitter,
        loss=(1 - len(replies) / len(window)) if window else 0.0,
        recent_loss=(sum(v is None for v in recent) / len(recent)) if recent else 0.0,
    )


def diagnose(gateway: TargetStats | None, internet: TargetStats) -> str:
    if internet.samples < 3:
        return "unknown"
    gateway_answers = gateway is not None and gateway.replies_total > 0 and gateway.samples >= 3
    if internet.recent_loss == 1.0:
        if gateway_answers and gateway.recent_loss == 1.0:
            return "offline_local"
        return "offline_internet" if gateway_answers else "offline"
    if internet.samples < MIN_SAMPLES_FOR_QUALITY:
        return "unknown"
    if gateway_answers and gateway.samples >= MIN_SAMPLES_FOR_QUALITY and (
        gateway.loss >= LOSS_THRESHOLD or (gateway.avg or 0) > 50 or (gateway.jitter or 0) > 30
    ):
        return "unstable_local"
    if internet.loss >= LOSS_THRESHOLD or (internet.jitter or 0) > 40:
        return "unstable_internet"
    if (internet.avg or 0) > 150:
        return "slow_internet"
    return "good"


DIAGNOSIS_LEVEL = {
    "unknown": "unknown",
    "good": "good",
    "slow_internet": "warn",
    "unstable_internet": "warn",
    "unstable_local": "warn",
    "offline": "bad",
    "offline_internet": "bad",
    "offline_local": "bad",
}


class LatencyTarget:
    def __init__(self, key: str, resolve_host: Callable[[], str | None], pinger_factory: Callable[[], Pinger],
                 interval: float, clock: Callable[[], float] = time.time):
        self.key = key
        self.host: str | None = None
        self._resolve_host = resolve_host
        self._pinger_factory = pinger_factory
        self._interval = interval
        self._clock = clock
        self._samples: deque[tuple[float, float | None]] = deque(maxlen=HISTORY_LENGTH)
        self._replies_total = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"latency-{key}", daemon=True)
        self._host_checked = 0.0
        self._ip: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._replies_total = 0
            self._host_checked = 0.0
            self._ip = None

    def record(self, value: float | None, when: float | None = None) -> None:
        with self._lock:
            self._samples.append((when or self._clock(), value))
            if value is not None:
                self._replies_total += 1

    def _current_ip(self) -> str | None:
        now = time.monotonic()
        if self._ip is None or now - self._host_checked > 30:
            self._host_checked = now
            host = self._resolve_host()
            if host != self.host:
                with self._lock:
                    self._samples.clear()
                    self._replies_total = 0
            self.host = host
            self._ip = resolve_ipv4(host) if host else None
        return self._ip

    def _run(self) -> None:
        pinger = self._pinger_factory()
        while not self._stop.is_set():
            started = time.monotonic()
            ip = self._current_ip()
            if self.host:
                self.record(pinger.ping(ip, 1000) if ip else None)
            self._stop.wait(max(0.1, self._interval - (time.monotonic() - started)))

    def stats(self) -> TargetStats:
        with self._lock:
            values = [v for _, v in self._samples]
            replies_total = self._replies_total
        return compute_stats(values, replies_total)

    def history(self) -> list[tuple[float, float | None]]:
        with self._lock:
            return list(self._samples)


class LatencyMonitor:
    def __init__(self, internet_host: str, pinger_factory: Callable[[], Pinger] = create_pinger,
                 gateway_lookup: Callable[[], str | None] = default_gateway, interval: float = 2.0):
        self._internet_host = internet_host
        self.gateway = LatencyTarget("gateway", gateway_lookup, pinger_factory, interval)
        self.internet = LatencyTarget("internet", lambda: self._internet_host, pinger_factory, interval)

    def start(self) -> None:
        self.gateway.start()
        self.internet.start()

    def stop(self) -> None:
        self.gateway.stop()
        self.internet.stop()

    def set_internet_host(self, host: str) -> None:
        if host != self._internet_host:
            self._internet_host = host
            self.internet.reset()

    def diagnosis(self) -> str:
        gateway = self.gateway.stats() if self.gateway.host else None
        return diagnose(gateway, self.internet.stats())

    def snapshot(self, with_history: bool = True) -> dict:
        code = self.diagnosis()
        result = {"diagnosis": code, "level": DIAGNOSIS_LEVEL[code], "targets": {}}
        for target in (self.gateway, self.internet):
            entry = {"host": target.host, **target.stats().to_dict()}
            if with_history:
                entry["history"] = [[round(ts, 1), None if v is None else round(v, 2)] for ts, v in target.history()]
            result["targets"][target.key] = entry
        return result
