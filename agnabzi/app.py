"""Command line entry point and application lifecycle."""

from __future__ import annotations

import argparse
import atexit
import http.client
import json
import logging
import logging.handlers
import os
import secrets
import sys
import threading
import time
import webbrowser

from agnabzi import APP_ID, APP_NAME, __version__
from agnabzi.api import Api
from agnabzi.collector import Collector
from agnabzi.events import Event, EventLog
from agnabzi.latency import LatencyMonitor, create_pinger
from agnabzi.netinfo import ReverseDns, default_gateway
from agnabzi.platforms import create_platform
from agnabzi.server import DashboardServer
from agnabzi.storage import Storage
from agnabzi.system import IS_WINDOWS, data_dir, is_admin, package_dir
from agnabzi.texts import format_rate, notification, text

log = logging.getLogger("agnabzi")

DEFAULT_PORT = 8765


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agnabzi", description=f"{APP_NAME}: network traffic monitor")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="dashboard port (default: %(default)s)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard on start")
    parser.add_argument("--background", action="store_true", help="start silently (used for autostart)")
    parser.add_argument("--demo", action="store_true", help="run with simulated traffic")
    parser.add_argument("--console", action="store_true", help="log to the console and skip the tray icon")
    parser.add_argument("--replace", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--selftest", nargs="?", const="-", metavar="REPORT",
                        help="check the environment and exit; optionally write a JSON report")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def configure_logging(console: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(data_dir(), "netpulse.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError:
        pass
    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.setLevel(logging.WARNING)
        root.addHandler(stream)


def say(message: str) -> None:
    if sys.stdout is None:
        return
    if not sys.stdout.isatty() and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        print(message, flush=True)
    except (OSError, UnicodeEncodeError):
        print(message.encode("ascii", "replace").decode(), flush=True)


def probe_running_instance(port: int) -> bool:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", "/api/ping")
        response = conn.getresponse()
        return response.status == 200 and json.loads(response.read()).get("app") == APP_ID
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def dashboard_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


class Application:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.token = secrets.token_urlsafe(32)
        self._shutdown = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._cleanup_started = False
        self._cleanup_done = threading.Event()
        self._tray = None
        self._traffic = None
        self.per_app_reason = ""

    # startup ---------------------------------------------------------------------

    def _bind_server(self) -> DashboardServer | None:
        port = self.args.port
        deadline = time.monotonic() + (20 if self.args.replace else 0)
        while True:
            try:
                return DashboardServer(port, self.token)
            except OSError:
                if time.monotonic() < deadline:
                    time.sleep(0.4)
                    continue
            break
        if probe_running_instance(port):
            say(text("en", "already_running", name=APP_NAME))
            if not self.args.background:
                webbrowser.open(dashboard_url(port))
            return None
        for candidate in [*range(port + 1, port + 11), 0]:
            try:
                return DashboardServer(candidate, self.token)
            except OSError:
                continue
        raise RuntimeError("no free port for the dashboard")

    def _start_traffic_source(self):
        if not IS_WINDOWS:
            self.per_app_reason = "platform"
            return None
        if not is_admin():
            self.per_app_reason = "not_admin"
            return None
        from agnabzi.windows.etw import EtwTrafficSource

        source = EtwTrafficSource()
        try:
            source.start()
        except OSError as exc:
            log.warning("per-application capture unavailable: %s", exc)
            self.per_app_reason = "etw_failed"
            return None
        return source

    def _build(self, server: DashboardServer) -> None:
        demo = self.args.demo
        self.events = EventLog()
        if demo:
            from agnabzi import demo as demo_mode

            self.storage = Storage.in_memory()
            demo_mode.seed_history(self.storage)
            network = demo_mode.DemoNetwork()
            probe = demo_mode.DemoProbe(network)
            self._traffic = demo_mode.DemoTrafficSource(network)
            pinger_factory = demo_mode.DemoPinger
            gateway_lookup = lambda: demo_mode.DEMO_GATEWAY  # noqa: E731
            self.rdns = ReverseDns(static=demo_mode.DEMO_HOSTNAMES)
            speedtest_factory = demo_mode.DemoSpeedTest
            trace = demo_mode.demo_trace
        else:
            from agnabzi.probe import SystemProbe

            self.storage = Storage(os.path.join(data_dir(), "usage.json"))
            self.storage.prune()
            describe = None
            if IS_WINDOWS:
                from agnabzi.windows.api import file_description

                describe = file_description
            probe = SystemProbe(describe_executable=describe)
            self._traffic = self._start_traffic_source()
            pinger_factory = create_pinger
            gateway_lookup = default_gateway
            self.rdns = ReverseDns()
            speedtest_factory = trace = None

        settings = self.storage.settings
        self.latency = LatencyMonitor(settings["ping_target"], pinger_factory, gateway_lookup)
        self.platform = create_platform(demo)
        self.collector = Collector(probe, self.storage, self.events, self.rdns, self._traffic, self.latency)
        self.api = Api(
            collector=self.collector,
            storage=self.storage,
            events=self.events,
            latency=self.latency,
            rdns=self.rdns,
            platform=self.platform,
            pinger_factory=pinger_factory,
            demo=demo,
            port=server.port,
            request_quit=self.request_quit,
            per_app_reason=self.per_app_reason,
            speedtest_factory=speedtest_factory,
            trace=trace,
        )
        server.api = self.api
        self.server = server

    def _start_tray(self) -> None:
        if not IS_WINDOWS or self.args.console:
            return
        from agnabzi.windows.tray import TrayIcon

        icon_path = os.path.join(package_dir(), "web", "static", "img", "netpulse.ico")
        tray = TrayIcon(
            tooltip=APP_NAME,
            icon_path=icon_path if os.path.exists(icon_path) else None,
            menu=self._tray_menu,
            on_activate=self.open_dashboard,
            on_session_end=self.cleanup,
        )
        if tray.start():
            self._tray = tray
            self.events.subscribe(self._notify)
            threading.Thread(target=self._update_tray, name="tray-tooltip", daemon=True).start()

    def _tray_menu(self) -> list:
        from agnabzi.windows.tray import SEPARATOR, MenuItem

        language = self.storage.settings["language"]
        down, up = self.collector.tray_rates()
        unit = self.storage.settings["speed_unit"]
        items = [
            MenuItem(text(language, "open"), self.open_dashboard),
            MenuItem(text(language, "rates", down=format_rate(down, unit), up=format_rate(up, unit)), None),
            SEPARATOR,
        ]
        if self.platform.capabilities()["elevation"]:
            items.append(MenuItem(text(language, "elevate"), self.api.elevate))
        items.append(MenuItem(text(language, "quit"), self.request_quit))
        return items

    def _update_tray(self) -> None:
        while not self._shutdown.wait(2.0):
            if self._tray is None:
                return
            unit = self.storage.settings["speed_unit"]
            down, up = self.collector.tray_rates()
            self._tray.set_tooltip(f"{APP_NAME}\n↓ {format_rate(down, unit)}   ↑ {format_rate(up, unit)}")

    def _notify(self, event: Event) -> None:
        if self._tray is None:
            return
        settings = self.storage.settings
        content = notification(event, settings["language"], settings)
        if content:
            title, message, warning = content
            self._tray.notify(title, message, warning)

    # running ---------------------------------------------------------------------

    def open_dashboard(self) -> None:
        webbrowser.open(dashboard_url(self.server.port))

    def request_quit(self) -> None:
        # Give the HTTP response that triggered the quit a moment to reach the browser.
        threading.Timer(0.3, self._shutdown.set).start()

    def run(self) -> int:
        server = self._bind_server()
        if server is None:
            return 0
        self._build(server)
        atexit.register(self.cleanup)
        if IS_WINDOWS:
            from agnabzi.windows import api as winapi

            if winapi.has_console():
                winapi.on_console_close(self.cleanup)

        threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
        self.latency.start()
        self.collector.start()
        self._start_tray()

        url = dashboard_url(server.port)
        log.info("dashboard listening on %s (per-app capture: %s)", url, self._traffic is not None)
        say(text("en", "running", name=APP_NAME, version=__version__, url=url))
        say(text("en", "stop_hint"))
        if not (self.args.no_browser or self.args.background):
            webbrowser.open(url)

        try:
            while not self._shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        self.cleanup()
        return 0

    def cleanup(self) -> None:
        with self._cleanup_lock:
            started, self._cleanup_started = self._cleanup_started, True
        if started:
            # Another thread (console close, session end) is already cleaning up; let it finish.
            self._cleanup_done.wait(15)
            return
        self._shutdown.set()
        steps = [
            ("collector", lambda: self.collector.stop()),
            ("latency", lambda: self.latency.stop()),
            ("traffic", lambda: self._traffic and self._traffic.stop()),
            ("storage", lambda: self.storage.save(force=True)),
            ("server", lambda: self.server.shutdown()),
            ("platform", lambda: self.platform.shutdown()),
            ("rdns", lambda: self.rdns.shutdown()),
            ("tray", lambda: self._tray and self._tray.stop()),
        ]
        for name, step in steps:
            try:
                step()
            except Exception:
                log.exception("shutdown step %s failed", name)
        log.info("stopped")
        self._cleanup_done.set()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.selftest:
        from agnabzi.selftest import run_selftest

        return run_selftest(args.selftest)
    configure_logging(console=args.console or sys.stdout is not None)
    try:
        return Application(args).run()
    except Exception:
        log.exception("fatal error")
        raise

