"""Download/upload throughput test against Cloudflare's public speed test endpoints."""

from __future__ import annotations

import contextlib
import http.client
import json
import re
import socket
import ssl
import statistics
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

DEFAULT_SERVER = "https://speed.cloudflare.com"
DOWNLOAD_CHUNK = 25_000_000
UPLOAD_CHUNK = 8_000_000
READ_SIZE = 64 * 1024
WARMUP_SECONDS = 1.5
_SERVER_TIMING = re.compile(r"dur=([\d.]+)")
_UPLOAD_BLOCK = b"\x00" * READ_SIZE
# The metadata endpoint answers 403 without a Referer from the speed test site.
HEADERS = {"User-Agent": "NetPulse", "Referer": "https://speed.cloudflare.com/"}

Progress = Callable[[dict[str, Any]], None]


class _Counter:
    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def add(self, amount: int) -> None:
        with self._lock:
            self.value += amount


class _UploadBody:
    """File-like request body that stops producing data once the deadline passes."""

    def __init__(self, size: int, counter: _Counter, deadline: float):
        self._remaining = size
        self._counter = counter
        self._deadline = deadline

    def read(self, amount: int = READ_SIZE) -> bytes:
        if self._remaining <= 0 or time.monotonic() >= self._deadline:
            return b""
        chunk = _UPLOAD_BLOCK[: min(amount, self._remaining, READ_SIZE)]
        self._remaining -= len(chunk)
        self._counter.add(len(chunk))
        return chunk


def parse_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Normalise /meta, whose ``colo`` is an object ({"iata", "city", ...}) on the live service."""
    colo = meta.get("colo")
    colo_info = colo if isinstance(colo, dict) else {"iata": colo or ""}
    return {
        "colo": str(colo_info.get("iata") or ""),
        "city": str(meta.get("city") or colo_info.get("city") or ""),
        "country": str(meta.get("country") or colo_info.get("cca2") or ""),
        "isp": str(meta.get("asOrganization") or ""),
        "ip": str(meta.get("clientIp") or ""),
    }


class SpeedTest:
    def __init__(self, server: str = DEFAULT_SERVER, duration: float = 8.0, streams: int = 4,
                 on_progress: Progress | None = None, timeout: float = 10.0):
        parts = urlsplit(server)
        self._secure = parts.scheme == "https"
        self._host = parts.hostname or ""
        self._port = parts.port
        self._duration = duration
        self._streams = streams
        self._timeout = timeout
        self._on_progress = on_progress or (lambda _: None)
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {"phase": "starting"}
        self._active: set[http.client.HTTPConnection] = set()
        self._active_lock = threading.Lock()

    def cancel(self) -> None:
        self._cancel.set()

    def _connection(self) -> http.client.HTTPConnection:
        if self._secure:
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=ssl.create_default_context()
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def _open_transfer(self) -> http.client.HTTPConnection:
        conn = self._connection()
        with self._active_lock:
            self._active.add(conn)
        return conn

    def _close_transfer(self, conn: http.client.HTTPConnection) -> None:
        with self._active_lock:
            self._active.discard(conn)
        conn.close()

    def _abort_transfers(self) -> None:
        """Unblock workers stuck in a send or receive once the phase is over."""
        with self._active_lock:
            active = list(self._active)
        for conn in active:
            if conn.sock is not None:
                with contextlib.suppress(OSError):
                    conn.sock.shutdown(socket.SHUT_RDWR)

    def _emit(self, **changes: Any) -> None:
        self._state.update(changes)
        self._on_progress(dict(self._state))

    def server_info(self) -> dict[str, Any]:
        conn = self._connection()
        try:
            conn.request("GET", "/meta", headers=HEADERS)
            response = conn.getresponse()
            if response.status == 200:
                return parse_meta(json.loads(response.read(64 * 1024)))
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        return {}

    def measure_latency(self, samples: int = 10) -> tuple[float | None, float | None]:
        conn = self._connection()
        results: list[float] = []
        try:
            for _ in range(samples):
                if self._cancel.is_set():
                    break
                started = time.perf_counter()
                conn.request("GET", "/__down?bytes=0", headers=HEADERS)
                response = conn.getresponse()
                elapsed = (time.perf_counter() - started) * 1000
                response.read()
                server_time = _SERVER_TIMING.search(response.getheader("Server-Timing") or "")
                if server_time:
                    elapsed = max(elapsed - float(server_time.group(1)), 0.1)
                results.append(elapsed)
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        if not results:
            return None, None
        # The first request includes the TLS handshake.
        steady = results[1:] or results
        jitter = statistics.fmean(abs(b - a) for a, b in zip(steady, steady[1:])) if len(steady) > 1 else 0.0
        return statistics.median(steady), jitter

    def _download_worker(self, counter: _Counter, deadline: float) -> None:
        while time.monotonic() < deadline and not self._cancel.is_set():
            conn = self._open_transfer()
            try:
                conn.request("GET", f"/__down?bytes={DOWNLOAD_CHUNK}", headers=HEADERS)
                response = conn.getresponse()
                while time.monotonic() < deadline and not self._cancel.is_set():
                    chunk = response.read(READ_SIZE)
                    if not chunk:
                        break
                    counter.add(len(chunk))
            except (OSError, http.client.HTTPException):
                time.sleep(0.2)
            finally:
                self._close_transfer(conn)

    def _upload_worker(self, counter: _Counter, deadline: float) -> None:
        while time.monotonic() < deadline and not self._cancel.is_set():
            conn = self._open_transfer()
            try:
                body = _UploadBody(UPLOAD_CHUNK, counter, deadline)
                headers = {
                    **HEADERS,
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(UPLOAD_CHUNK),
                }
                conn.request("POST", "/__up", body=body, headers=headers)
                conn.getresponse().read()
            except (OSError, http.client.HTTPException):
                pass
            finally:
                self._close_transfer(conn)

    def _throughput(self, phase: str, worker: Callable[[_Counter, float], None]) -> float | None:
        counter = _Counter()
        started = time.monotonic()
        deadline = started + self._duration
        threads = [
            threading.Thread(target=worker, args=(counter, deadline), daemon=True) for _ in range(self._streams)
        ]
        for thread in threads:
            thread.start()
        baseline: tuple[float, int] | None = None
        previous = (started, 0)
        while any(t.is_alive() for t in threads):
            time.sleep(0.25)
            now, total = time.monotonic(), counter.value
            if now >= deadline or self._cancel.is_set():
                self._abort_transfers()
            if baseline is None and now - started >= WARMUP_SECONDS:
                baseline = (now, total)
            current = (total - previous[1]) * 8 / max(now - previous[0], 1e-6)
            previous = (now, total)
            self._emit(phase=phase, current_bps=current, progress=min((now - started) / self._duration, 1.0))
        end, total = time.monotonic(), counter.value
        if self._cancel.is_set() or total == 0:
            return None
        begin_time, begin_bytes = baseline if baseline and end - baseline[0] > 0.5 else (started, 0)
        return (total - begin_bytes) * 8 / (end - begin_time)

    def run(self) -> dict[str, Any]:
        self._emit(phase="latency", progress=0.0)
        server = self.server_info()
        ping, jitter = self.measure_latency()
        if ping is None:
            self._emit(phase="error", error="unreachable")
            return {"ok": False, "error": "unreachable"}
        self._emit(phase="download", ping_ms=ping, jitter_ms=jitter, server=server, progress=0.0)
        download = self._throughput("download", self._download_worker)
        self._emit(phase="upload", download_bps=download, progress=0.0)
        upload = self._throughput("upload", self._upload_worker)
        result = {
            "ok": not self._cancel.is_set(),
            "ts": time.time(),
            "ping_ms": ping,
            "jitter_ms": jitter,
            "download_bps": download,
            "upload_bps": upload,
            "server": server,
        }
        self._emit(phase="done", upload_bps=upload, progress=1.0, result=result)
        return result
