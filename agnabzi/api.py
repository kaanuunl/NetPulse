"""Application service layer behind the HTTP API."""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Callable

from agnabzi import __version__, traceroute
from agnabzi.collector import Collector
from agnabzi.events import EventLog
from agnabzi.jobs import Job, JobManager
from agnabzi.latency import LatencyMonitor, Pinger
from agnabzi.netinfo import (
    ReverseDns,
    dns_lookup,
    dns_tampering_check,
    ip_info,
    is_valid_host,
    port_check,
    resolve_ipv4,
)
from agnabzi.platforms import BasePlatform, PlatformError
from agnabzi.speedtest import SpeedTest
from agnabzi.storage import SettingsError, Storage, billing_period
from agnabzi.system import data_dir

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code: str, status: int = 400, **details: Any):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details


def _require_host(value: Any) -> str:
    host = str(value or "").strip()
    if not is_valid_host(host):
        raise ApiError("invalid_host")
    return host


class Api:
    def __init__(
        self,
        *,
        collector: Collector,
        storage: Storage,
        events: EventLog,
        latency: LatencyMonitor,
        rdns: ReverseDns,
        platform: BasePlatform,
        pinger_factory: Callable[[], Pinger],
        demo: bool,
        port: int,
        request_quit: Callable[[], None],
        per_app_reason: str = "",
        speedtest_factory: Callable[..., Any] | None = None,
        trace: Callable[[str], Any] | None = None,
    ):
        self._collector = collector
        self._storage = storage
        self._events = events
        self._latency = latency
        self._rdns = rdns
        self._platform = platform
        self._pinger_factory = pinger_factory
        self._demo = demo
        self._port = port
        self._request_quit = request_quit
        self._per_app_reason = per_app_reason
        self._speedtest_factory = speedtest_factory or (lambda on_progress: SpeedTest(on_progress=on_progress))
        self._trace = trace or traceroute.trace
        self._jobs = JobManager()

    # dashboard data --------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        return {
            **self._platform.capabilities(),
            "per_app": self._collector.per_app_available,
            "per_app_reason": self._per_app_reason,
            "demo": self._demo,
            "version": __version__,
        }

    def overview(self, since_event: int, window: int = 300) -> dict[str, Any]:
        data = self._collector.overview(history_seconds=max(30, min(window, 600)))
        data["events"] = self._events.since(since_event)
        data["caps"] = self.capabilities()
        return data

    def apps(self) -> dict[str, Any]:
        blocked = self._storage.blocked_apps()
        apps = self._collector.apps_snapshot()
        for app in apps:
            app["blocked"] = app["exe"] in blocked
        live = {app["exe"] for app in apps}
        idle_blocked = [
            {"exe": exe, **info} for exe, info in blocked.items() if exe not in live
        ]
        return {"apps": apps, "blocked": idle_blocked, "per_app": self._collector.per_app_available}

    def security(self) -> dict[str, Any]:
        threats = self._collector.threats_snapshot()
        caps = self.capabilities()
        blocked = self._storage.blocked_apps()
        for finding in threats["findings"]:
            finding["blocked"] = finding["exe"] in blocked
        return {
            "threats": threats,
            "posture": {
                "admin": caps["admin"],
                "per_app": caps["per_app"],
                "per_app_reason": caps.get("per_app_reason", ""),
                "firewall": caps["firewall"],
                "autostart": self._platform.autostart_status(),
                "dpi": self._platform.dpi_status(),
                "demo": caps["demo"],
                "blocked_count": len(blocked),
            },
            "caps": caps,
        }

    def connections(self) -> dict[str, Any]:
        return {"connections": self._collector.connections_snapshot()}

    def adapters(self) -> dict[str, Any]:
        return {"adapters": self._collector.adapters_snapshot()}

    def history(self, days: int) -> dict[str, Any]:
        days = max(7, min(days, 365))
        settings = self._storage.settings
        start, end = billing_period(date.today(), settings["quota_reset_day"])
        return {
            "summary": self._storage.usage_summary(),
            "daily": self._storage.daily(days),
            "monthly": self._storage.monthly(12),
            "top_apps": self._storage.top_apps(start, end),
            "speedtests": self._storage.speedtests(),
        }

    def export_csv(self) -> str:
        return self._storage.export_csv()

    def reset_history(self) -> dict[str, Any]:
        self._storage.reset_history()
        self._storage.save(force=True)
        return {"ok": True}

    # settings --------------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        return {
            "settings": self._storage.settings,
            "autostart": self._platform.autostart_status(),
            "data_dir": data_dir() if not self._demo else "",
            "caps": self.capabilities(),
        }

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = self._storage.update_settings(changes)
        except SettingsError as exc:
            raise ApiError("invalid_setting", field=exc.field) from exc
        if "ping_target" in changes:
            self._latency.set_internet_host(settings["ping_target"])
        self._storage.save(force=True)
        return self.settings()

    def set_autostart(self, enabled: bool) -> dict[str, Any]:
        try:
            return {"autostart": self._platform.set_autostart(bool(enabled))}
        except PlatformError as exc:
            raise ApiError(exc.code) from exc
        except OSError as exc:
            raise ApiError("failed", detail=str(exc)) from exc

    # applications ------------------------------------------------------------------

    def _exe_for(self, key: str) -> tuple[str, str]:
        app = self._collector.app_by_key(str(key))
        if app is not None and app.exe:
            return app.exe, app.display_name
        blocked = self._storage.blocked_apps()
        for exe, info in blocked.items():
            if exe.lower() == str(key).lower():
                return exe, info.get("name", exe)
        raise ApiError("unknown_app", status=404)

    def block_app(self, key: str) -> dict[str, Any]:
        exe, name = self._exe_for(key)
        try:
            self._platform.block(exe)
        except PlatformError as exc:
            raise ApiError(exc.code) from exc
        self._storage.set_blocked(exe, {"name": name, "since": time.time()})
        self._events.add("blocked", "info", app=name, exe=exe)
        return {"ok": True}

    def unblock_app(self, key: str) -> dict[str, Any]:
        exe, name = self._exe_for(key)
        try:
            self._platform.unblock(exe)
        except PlatformError as exc:
            raise ApiError(exc.code) from exc
        self._storage.set_blocked(exe, None)
        self._events.add("unblocked", "info", app=name, exe=exe)
        return {"ok": True}

    def reveal_app(self, key: str) -> dict[str, Any]:
        exe, _ = self._exe_for(key)
        try:
            self._platform.reveal(exe)
        except PlatformError as exc:
            raise ApiError(exc.code) from exc
        return {"ok": True}

    def icon(self, key: str) -> bytes | None:
        app = self._collector.app_by_key(key)
        if app is not None:
            exe = app.exe
        else:
            exe = key if key.lower().endswith(".exe") and os.path.isabs(key) else ""
        return self._platform.icon(exe) if exe else None

    # tools -----------------------------------------------------------------------

    def dns(self, name: Any) -> dict[str, Any]:
        return dns_lookup(_require_host(name))

    def dns_check(self, name: Any) -> dict[str, Any]:
        return dns_tampering_check(_require_host(name))

    def port(self, host: Any, port: Any) -> dict[str, Any]:
        try:
            number = int(port)
        except (TypeError, ValueError):
            raise ApiError("invalid_port") from None
        if not 1 <= number <= 65535:
            raise ApiError("invalid_port")
        return port_check(_require_host(host), number)

    def ping(self, host: Any, count: Any = 4) -> dict[str, Any]:
        host = _require_host(host)
        ip = resolve_ipv4(host)
        if ip is None:
            raise ApiError("unresolved")
        pinger = self._pinger_factory()
        replies = [pinger.ping(ip, 1000) for _ in range(max(1, min(int(count or 4), 10)))]
        received = [r for r in replies if r is not None]
        return {
            "ip": ip,
            "replies": replies,
            "loss": 1 - len(received) / len(replies),
            "min": min(received) if received else None,
            "avg": sum(received) / len(received) if received else None,
            "max": max(received) if received else None,
        }

    def ip_details(self, ip: Any = None) -> dict[str, Any]:
        if ip:
            ip = _require_host(ip)
        return ip_info(ip or None)

    def start_traceroute(self, host: Any) -> dict[str, Any]:
        host = _require_host(host)

        def work(job: Job) -> dict[str, Any]:
            job.update(host=host)
            for hop in self._trace(host):
                if job.cancelled.is_set():
                    break
                if hop["ip"] and "host" not in hop:
                    hop["host"] = self._rdns.resolve_now(hop["ip"], timeout=1.5)
                job.append(hop)
            return {"hops": len(job.items)}

        return self._jobs.start("traceroute", work).to_dict()

    def start_speedtest(self) -> dict[str, Any]:
        def work(job: Job) -> dict[str, Any]:
            test = self._speedtest_factory(lambda state: job.update(**state))
            job.on_cancel(test.cancel)
            result = test.run()
            if result.get("ok"):
                self._storage.add_speedtest(result)
                self._storage.save(force=True)
            return result

        return self._jobs.start("speedtest", work, exclusive=True).to_dict()

    def job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError("unknown_job", status=404)
        return job.to_dict()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError("unknown_job", status=404)
        job.cancel()
        return job.to_dict()

    # system ----------------------------------------------------------------------

    def elevate(self) -> dict[str, Any]:
        if self._platform.admin:
            return {"ok": True, "restarting": False}
        try:
            accepted = self._platform.elevate(["--port", str(self._port), "--replace", "--no-browser"])
        except PlatformError as exc:
            raise ApiError(exc.code) from exc
        if not accepted:
            raise ApiError("elevation_declined")
        self._request_quit()
        return {"ok": True, "restarting": True}

    def quit(self) -> dict[str, Any]:
        self._request_quit()
        return {"ok": True}
