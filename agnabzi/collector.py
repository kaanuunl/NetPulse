"""The sampling loop that turns raw counters into rates, totals and events."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from agnabzi.events import EventLog
from agnabzi.latency import DIAGNOSIS_LEVEL, LatencyMonitor
from agnabzi.models import AppStats, Connection
from agnabzi.netinfo import ReverseDns, classify_ip, is_internet, is_loopback, service_name
from agnabzi.probe import is_virtual_adapter
from agnabzi.storage import Storage
from agnabzi.threats import SENSITIVE_LISTEN_PORTS, AppActivity, analyze, summarize
from agnabzi.traffic import TrafficSource, pack_ip

log = logging.getLogger(__name__)

HISTORY_SECONDS = 600
REMOTES_PER_APP = 200
IDLE_APP_EXPIRY = 900
QUOTA_LEVELS = (100, 80)
QUALITY_ALERT_COOLDOWN = 600


def _smooth(previous: float, current: float) -> float:
    value = previous * 0.4 + current * 0.6
    return value if value >= 1 else 0.0


class Collector:
    def __init__(
        self,
        probe: Any,
        storage: Storage,
        events: EventLog,
        rdns: ReverseDns,
        traffic: TrafficSource | None = None,
        latency: LatencyMonitor | None = None,
        interval: float = 1.0,
        connection_interval: float = 2.0,
        clock: Callable[[], float] = time.time,
    ):
        self._probe = probe
        self._storage = storage
        self._events = events
        self._rdns = rdns
        self._traffic = traffic
        self._latency = latency
        self._interval = interval
        self._connection_interval = connection_interval
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="collector", daemon=True)

        self.started = clock()
        self.history: deque[tuple[float, float, float]] = deque(maxlen=HISTORY_SECONDS)
        self.rate_down = 0.0
        self.rate_up = 0.0
        self.session_rx = 0
        self.session_tx = 0
        self.apps: dict[str, AppStats] = {}
        self.connections: list[Connection] = []
        self.adapter_rates: dict[str, tuple[float, float]] = {}

        self._last_counters: dict[str, tuple[int, int]] | None = None
        self._last_tick: float | None = None
        self._last_connection_scan = 0.0
        self._local_packed: set[bytes] = set()
        self._local_refreshed = 0.0
        self._quota_checked = 0.0
        self._connectivity = "unknown"
        self._offline_since: float | None = None
        self._last_quality_alert = 0.0
        self._baseline_done = storage.has_known_apps()

    @property
    def per_app_available(self) -> bool:
        return self._traffic is not None

    # lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("collector tick failed")
            deadline += self._interval
            delay = deadline - time.monotonic()
            if delay < 0:
                deadline, delay = time.monotonic(), 0
            self._stop.wait(delay)

    # sampling --------------------------------------------------------------------

    def _excluded_adapters(self, names: list[str]) -> set[str]:
        configured = self._storage.settings["excluded_adapters"]
        if configured is None:
            return {name for name in names if is_virtual_adapter(name)}
        return set(configured)

    def tick(self, now: float | None = None) -> None:
        now = now or self._clock()
        counters = self._probe.interface_counters()
        excluded = self._excluded_adapters(list(counters))
        delta_rx = delta_tx = 0
        with self._lock:
            elapsed = now - self._last_tick if self._last_tick else 0.0
            if self._last_counters is not None and elapsed > 0:
                rates = {}
                for name, (rx, tx) in counters.items():
                    previous = self._last_counters.get(name)
                    if previous is None:
                        continue
                    grown_rx = max(rx - previous[0], 0)
                    grown_tx = max(tx - previous[1], 0)
                    rates[name] = (grown_rx / elapsed, grown_tx / elapsed)
                    if name not in excluded:
                        delta_rx += grown_rx
                        delta_tx += grown_tx
                self.adapter_rates = rates
                self.rate_down = delta_rx / elapsed
                self.rate_up = delta_tx / elapsed
                self.history.append((now, self.rate_down, self.rate_up))
                self.session_rx += delta_rx
                self.session_tx += delta_tx
            self._last_counters = counters
            self._last_tick = now

        self._storage.add_usage(delta_rx, delta_tx, now)
        if elapsed > 0:
            self._account_processes(now, elapsed)
        if now - self._last_connection_scan >= self._connection_interval:
            self._scan_connections(now)
        self._check_quota(now)
        self._check_connectivity(now)
        self._storage.maybe_save()

    def _local_addresses(self, now: float) -> set[bytes]:
        if now - self._local_refreshed > 30:
            packed = (pack_ip(ip) for ip in self._probe.local_addresses())
            self._local_packed = {p for p in packed if p}
            self._local_refreshed = now
        return self._local_packed

    def _app_for_pid(self, pid: int, now: float) -> AppStats:
        info = self._probe.process_info(pid)
        app = self.apps.get(info.key)
        if app is None:
            app = self.apps[info.key] = AppStats(info.key, info.name, info.exe, info.title, first_seen=now)
            self._storage.remember_app_meta(info.name, info.title, info.exe)
        app.pids.add(pid)
        return app

    def _account_processes(self, now: float, elapsed: float) -> None:
        if self._traffic is None:
            return
        samples = self._traffic.drain(self._local_addresses(now))
        per_app: dict[str, list[int]] = {}
        internet_apps: dict[str, str] = {}
        with self._lock:
            for sample in samples:
                if is_loopback(sample.remote_ip):
                    continue
                app = self._app_for_pid(sample.pid, now)
                app.rx_total += sample.rx
                app.tx_total += sample.tx
                app.last_active = now
                remote = app.remote(sample.remote_ip)
                remote.rx += sample.rx
                remote.tx += sample.tx
                remote.last_seen = now
                bucket = per_app.setdefault(app.key, [0, 0])
                bucket[0] += sample.rx
                bucket[1] += sample.tx
                if is_internet(sample.remote_ip):
                    internet_apps.setdefault(app.key, sample.remote_ip)
            for app in self.apps.values():
                rx, tx = per_app.get(app.key, (0, 0))
                app.rx_rate = _smooth(app.rx_rate, rx / elapsed)
                app.tx_rate = _smooth(app.tx_rate, tx / elapsed)
            names = {key: self.apps[key].name for key in per_app}
        for key, (rx, tx) in per_app.items():
            self._storage.add_app_usage(names[key], rx, tx, now)
        self._announce_new_apps(internet_apps, now)

    def _scan_connections(self, now: float) -> None:
        self._last_connection_scan = now
        connections = self._probe.connections()
        internet_apps: dict[str, str] = {}
        with self._lock:
            for app in self.apps.values():
                app.connections = 0
                app.listening = 0
                for remote in app.remotes.values():
                    remote.connections = 0
                    remote.ports.clear()
            for conn in connections:
                if conn.pid == 0:
                    continue
                app = self._app_for_pid(conn.pid, now)
                if conn.is_listening:
                    app.listening += 1
                    continue
                if not conn.remote_ip or is_loopback(conn.remote_ip):
                    continue
                app.connections += 1
                remote = app.remote(conn.remote_ip)
                remote.connections += 1
                remote.ports.add(conn.remote_port)
                remote.last_seen = now
                if is_internet(conn.remote_ip):
                    internet_apps.setdefault(app.key, conn.remote_ip)
            self.connections = connections
            self._expire_apps(now)
        self._probe.prune_processes()
        self._announce_new_apps(internet_apps, now)

    def _expire_apps(self, now: float) -> None:
        for key, app in list(self.apps.items()):
            app.trim_remotes(REMOTES_PER_APP)
            idle = now - max(app.last_active, app.first_seen)
            if app.connections == 0 and app.listening == 0 and app.rx_total + app.tx_total == 0 and idle > 60:
                del self.apps[key]
            elif app.connections == 0 and app.listening == 0 and idle > IDLE_APP_EXPIRY and not app.rx_rate:
                app.pids.clear()

    def _announce_new_apps(self, internet_apps: dict[str, str], now: float) -> None:
        fresh = [key for key in internet_apps if not self._storage.is_known_app(key)]
        for key in fresh:
            self._storage.remember_app(key, now)
            if not self._baseline_done:
                continue
            app = self.apps.get(key)
            if app is None:
                continue
            ip = internet_apps[key]
            self._events.add(
                "new_app", "warn", app=app.display_name, name=app.name, exe=app.exe, key=key,
                ip=ip, host=self._rdns.lookup(ip),
            )
        if not self._baseline_done and internet_apps:
            self._baseline_done = True
            self._events.add("baseline", "info", count=len(internet_apps))

    def _check_quota(self, now: float) -> None:
        if now - self._quota_checked < 30:
            return
        self._quota_checked = now
        summary = self._storage.usage_summary()
        ratio = summary["quota"]["used_ratio"]
        if ratio is None:
            return
        period = summary["period"]["start"]
        crossed = [level for level in QUOTA_LEVELS if ratio * 100 >= level]
        if not crossed:
            return
        announce = self._storage.quota_alert_due(period, crossed[0])
        for level in crossed[1:]:
            self._storage.quota_alert_due(period, level)
        if announce:
            self._events.add(
                "quota", "bad" if crossed[0] >= 100 else "warn", threshold=crossed[0],
                used=summary["period"]["rx"] + summary["period"]["tx"], quota=summary["quota"]["bytes"],
            )

    def _check_connectivity(self, now: float) -> None:
        if self._latency is None:
            return
        code = self._latency.diagnosis()
        level = DIAGNOSIS_LEVEL[code]
        previous = self._connectivity
        if level == "unknown":
            return
        if level == "bad" and previous != "bad":
            self._offline_since = now
            self._events.add("offline", "bad", code=code)
        elif previous == "bad" and level != "bad":
            self._events.add("online", "good", seconds=round(now - (self._offline_since or now)))
            self._offline_since = None
        elif level == "warn" and now - self._last_quality_alert > QUALITY_ALERT_COOLDOWN:
            self._last_quality_alert = now
            self._events.add("quality", "warn", code=code)
        self._connectivity = level

    # snapshots -------------------------------------------------------------------

    def _app_summary(self, app: AppStats) -> dict[str, Any]:
        return {
            "key": app.key,
            "name": app.name,
            "title": app.title,
            "exe": app.exe,
            "pids": sorted(app.pids),
            "rx_rate": app.rx_rate,
            "tx_rate": app.tx_rate,
            "rx_total": app.rx_total,
            "tx_total": app.tx_total,
            "connections": app.connections,
            "listening": app.listening,
            "first_seen": app.first_seen,
            "last_active": app.last_active,
        }

    def overview(self, history_seconds: int = 300) -> dict[str, Any]:
        with self._lock:
            history = list(self.history)[-history_seconds:]
            apps = list(self.apps.values())
            if self.per_app_available:
                ranked = sorted(apps, key=lambda a: (a.rx_rate + a.tx_rate, a.rx_total + a.tx_total), reverse=True)
            else:
                ranked = sorted(apps, key=lambda a: a.connections, reverse=True)
            top = [self._app_summary(a) for a in ranked[:6] if a.connections or a.rx_rate + a.tx_rate or a.rx_total]
            security = summarize(analyze(self._threat_activities()))
            overview = {
                "ts": self._clock(),
                "rates": {"down": self.rate_down, "up": self.rate_up},
                "history": [[round(ts, 2), round(d), round(u)] for ts, d, u in history],
                "session": {"started": self.started, "rx": self.session_rx, "tx": self.session_tx},
                "top_apps": top,
                "security": security,
                "counts": {
                    "apps": sum(1 for a in apps if a.connections or a.rx_rate + a.tx_rate),
                    "connections": sum(1 for c in self.connections if c.remote_ip and c.status != "TIME_WAIT"),
                    "listening": sum(1 for c in self.connections if c.is_listening),
                    "threats": security["counts"]["high"] + security["counts"]["medium"],
                },
            }
        overview["usage"] = self._storage.usage_summary()
        overview["latency"] = self._latency.snapshot() if self._latency else None
        return overview

    def _threat_activities(self) -> list[AppActivity]:
        exposed: dict[str, dict[int, str]] = {}
        for conn in self.connections:
            if not (conn.is_listening and conn.pid):
                continue
            service = SENSITIVE_LISTEN_PORTS.get(conn.local_port)
            if service and classify_ip(conn.local_ip) == "unspecified":
                info = self._probe.process_info(conn.pid)
                exposed.setdefault(info.key, {})[conn.local_port] = service
        activities = []
        for app in self.apps.values():
            ips: set[str] = set()
            ports: set[int] = set()
            for ip, remote in app.remotes.items():
                if remote.connections > 0 and classify_ip(ip) == "public":
                    ips.add(ip)
                    ports |= remote.ports
            activities.append(AppActivity(
                key=app.key, name=app.display_name, exe=app.exe, has_publisher=bool(app.title),
                internet_ips=frozenset(ips), internet_ports=frozenset(ports),
                exposed_services=exposed.get(app.key, {}),
            ))
        return activities

    def threats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            findings = analyze(self._threat_activities())
            scanned = len(self.apps)
        return {
            "findings": [f.to_dict() for f in findings],
            "summary": summarize(findings),
            "scanned": scanned,
            "ts": self._clock(),
        }

    def apps_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for app in self.apps.values():
                entry = self._app_summary(app)
                remotes = sorted(
                    app.remotes.values(), key=lambda r: (r.connections > 0, r.rx + r.tx, r.last_seen), reverse=True
                )[:40]
                entry["remotes"] = [
                    {
                        "ip": r.ip,
                        "host": self._rdns.lookup(r.ip),
                        "scope": classify_ip(r.ip),
                        "rx": r.rx,
                        "tx": r.tx,
                        "connections": r.connections,
                        "ports": [{"port": p, "service": service_name(p)} for p in sorted(r.ports)],
                        "last_seen": r.last_seen,
                    }
                    for r in remotes
                ]
                result.append(entry)
            return result

    def connections_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            connections = list(self.connections)
        rows = []
        for conn in connections:
            info = self._probe.process_info(conn.pid) if conn.pid else None
            rows.append({
                "proto": conn.proto,
                "family": conn.family,
                "local_ip": conn.local_ip,
                "local_port": conn.local_port,
                "remote_ip": conn.remote_ip,
                "remote_port": conn.remote_port,
                "status": conn.status,
                "pid": conn.pid,
                "app": info.display_name if info else "",
                "name": info.name if info else "",
                "key": info.key if info else "",
                "exe": info.exe if info else "",
                "host": self._rdns.lookup(conn.remote_ip) if conn.remote_ip else "",
                "scope": classify_ip(conn.remote_ip) if conn.remote_ip else "",
                "listen_scope": classify_ip(conn.local_ip) if conn.is_listening else "",
                "listening": conn.is_listening,
                "service": service_name(conn.remote_port if conn.remote_ip else conn.local_port),
            })
        return rows

    def adapters_snapshot(self) -> list[dict[str, Any]]:
        interfaces = self._probe.interfaces()
        excluded = self._excluded_adapters([i["name"] for i in interfaces])
        with self._lock:
            rates = dict(self.adapter_rates)
        for entry in interfaces:
            rx, tx = rates.get(entry["name"], (0.0, 0.0))
            entry.update(rx_rate=rx, tx_rate=tx, counted=entry["name"] not in excluded)
        return interfaces

    def app_by_key(self, key: str) -> AppStats | None:
        with self._lock:
            return self.apps.get(key)

    def tray_rates(self) -> tuple[float, float]:
        with self._lock:
            return self.rate_down, self.rate_up
