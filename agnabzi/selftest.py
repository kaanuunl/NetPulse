"""Environment checks for ``--selftest``; used by CI and handy for bug reports."""

from __future__ import annotations

import http.client
import json
import os
import platform
import sys
import tempfile
import traceback
from typing import Any, Callable

from agnabzi import __version__
from agnabzi.system import IS_WINDOWS, is_admin, static_dir

REQUIRED_STATIC = ("index.html", "css/app.css", "js/main.js", "img/favicon.svg")


def _check(results: dict[str, Any], name: str, fn: Callable[[], Any], required: bool = True) -> None:
    try:
        detail = fn()
        results[name] = {"ok": True, "required": required, "detail": detail}
    except Exception as exc:
        results[name] = {
            "ok": False,
            "required": required,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=3),
        }


def _static_files() -> list[str]:
    missing = [f for f in REQUIRED_STATIC if not os.path.isfile(os.path.join(static_dir(), f))]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    return list(REQUIRED_STATIC)


def _storage_roundtrip() -> str:
    from agnabzi.storage import Storage

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "usage.json")
        store = Storage(path)
        store.add_usage(1000, 500)
        store.save(force=True)
        reloaded = Storage(path).usage_summary()["today"]
        if reloaded != {"rx": 1000, "tx": 500}:
            raise AssertionError(reloaded)
    return "ok"


def _interfaces() -> dict[str, Any]:
    from agnabzi.probe import SystemProbe

    probe = SystemProbe()
    counters = probe.interface_counters()
    return {"interfaces": len(counters), "connections": len(probe.connections())}


def _ping_loopback() -> float:
    from agnabzi.latency import create_pinger

    rtt = create_pinger().ping("127.0.0.1", 1000)
    if rtt is None:
        raise TimeoutError("no reply from 127.0.0.1")
    return rtt


def _gateway() -> str | None:
    from agnabzi.netinfo import default_gateway

    return default_gateway()


def _http_server() -> dict[str, int]:
    import threading

    from agnabzi.server import DashboardServer

    server = DashboardServer(0, "selftest-token")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    statuses = {}
    try:
        for label, path, headers in (
            ("ping", "/api/ping", {}),
            ("page", "/", {}),
            ("no_token", "/api/overview", {}),
        ):
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            response.read()
            statuses[label] = response.status
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
    expected = {"ping": 200, "page": 200, "no_token": 403}
    if statuses != expected:
        raise AssertionError(statuses)
    return statuses


def _windows_description() -> str:
    from agnabzi.windows.api import file_description

    return file_description(sys.executable)


def _windows_icon() -> int:
    from agnabzi.windows.icons import IconCache

    cache = IconCache()
    try:
        png = cache.get(sys.executable)
    finally:
        cache.shutdown()
    if not png or not png.startswith(b"\x89PNG"):
        raise AssertionError("no icon extracted")
    return len(png)


def _windows_etw() -> str:
    from agnabzi.windows.etw import EtwTrafficSource

    source = EtwTrafficSource(session_name="NetPulse-SelfTest")
    source.start()
    source.stop()
    return "session started and stopped"


def run_selftest(destination: str) -> int:
    results: dict[str, Any] = {}
    _check(results, "static_files", _static_files)
    _check(results, "storage", _storage_roundtrip)
    _check(results, "interfaces", _interfaces)
    _check(results, "ping_loopback", _ping_loopback)
    _check(results, "http_server", _http_server)
    _check(results, "gateway", _gateway, required=False)
    if IS_WINDOWS:
        _check(results, "file_description", _windows_description, required=False)
        _check(results, "icon_extraction", _windows_icon, required=False)
        if is_admin():
            _check(results, "etw_session", _windows_etw)

    passed = all(entry["ok"] for entry in results.values() if entry["required"])
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "admin": is_admin(),
        "passed": passed,
        "checks": results,
    }
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if destination == "-":
        if sys.stdout is not None:
            print(payload)
    else:
        with open(destination, "w", encoding="utf-8") as fh:
            fh.write(payload)
    return 0 if passed else 1
