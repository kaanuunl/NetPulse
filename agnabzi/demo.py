"""Simulated network activity for ``--demo``: previews, screenshots and UI work."""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agnabzi.models import Connection, ProcessInfo
from agnabzi.storage import Storage
from agnabzi.traffic import TrafficSample

DEMO_GATEWAY = "192.168.1.1"
DEMO_LOCAL_IP = "192.168.1.34"


@dataclass
class DemoApp:
    pid: int
    name: str
    exe: str
    title: str
    down: float
    up: float
    remotes: dict[str, tuple[str, int]]
    pattern: str = "steady"
    appears_after: float = 0.0
    listens: list[tuple[str, int]] = field(default_factory=list)


DEMO_APPS = [
    DemoApp(4120, "chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe", "Google Chrome",
            420_000, 38_000, {
                "142.250.187.110": ("fra24s06-in-f14.1e100.net", 443),
                "172.217.19.100": ("ist02s05-in-f4.1e100.net", 443),
                "151.101.65.140": ("reddit.map.fastly.net", 443),
                "140.82.121.4": ("lb-140-82-121-4-fra.github.com", 443),
            }, "bursty"),
    DemoApp(7384, "steam.exe", r"C:\Program Files (x86)\Steam\steam.exe", "Steam",
            3_600_000, 60_000, {
                "23.215.0.137": ("cache6-fra1.steamcontent.com", 443),
                "155.133.248.39": ("ext1-fra1.steamserver.net", 27017),
            }, "wave", listens=[("0.0.0.0", 27036)]),
    DemoApp(9216, "Spotify.exe", r"C:\Users\Demo\AppData\Roaming\Spotify\Spotify.exe", "Spotify",
            46_000, 4_000, {
                "35.186.224.25": ("25.224.186.35.bc.googleusercontent.com", 443),
                "104.199.65.124": ("ap-gew1.spotify.com", 4070),
            }, "steady", listens=[("0.0.0.0", 57621)]),
    DemoApp(5580, "Discord.exe", r"C:\Users\Demo\AppData\Local\Discord\app-1.0.9164\Discord.exe", "Discord",
            14_000, 11_000, {
                "162.159.130.234": ("gateway.discord.gg", 443),
                "66.22.214.137": ("frankfurt10432.discord.media", 50004),
            }, "steady"),
    DemoApp(6640, "OneDrive.exe", r"C:\Program Files\Microsoft OneDrive\OneDrive.exe", "Microsoft OneDrive",
            18_000, 280_000, {
                "13.107.42.12": ("onedrive.live.com", 443),
                "20.190.160.2": ("login.live.com", 443),
            }, "pulse"),
    DemoApp(1812, "svchost.exe", r"C:\Windows\System32\svchost.exe", "Host Process for Windows Services",
            90_000, 9_000, {
                "13.107.4.50": ("dl.delivery.mp.microsoft.com", 80),
                "20.54.232.160": ("fe3cr.delivery.mp.microsoft.com", 443),
            }, "pulse", listens=[("0.0.0.0", 135), ("0.0.0.0", 5353), ("0.0.0.0", 5355)]),
    DemoApp(8032, "msedge.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "Microsoft Edge",
            22_000, 5_000, {"204.79.197.200": ("www-bing-com.dual-a-0001.a-msedge.net", 443)}, "bursty"),
    DemoApp(4, "System", "", "", 3_000, 2_000, {"192.168.1.20": ("nas.local", 445)}, "steady",
            listens=[("0.0.0.0", 445), ("0.0.0.0", 139)]),
    DemoApp(11002, "svhost.exe", r"C:\Users\Demo\AppData\Local\Temp\svhost.exe", "",
            26_000, 140_000, {
                "45.133.1.77": ("", 443),
                "185.220.101.47": ("", 8443),
            }, "pulse", appears_after=6.0),
    DemoApp(10456, "qbittorrent.exe", r"C:\Program Files\qBittorrent\qbittorrent.exe", "qBittorrent",
            1_400_000, 380_000, {
                "88.230.14.77": ("88.230.14.77.dynamic.ttnet.com.tr", 51413),
                "78.180.96.12": ("78.180.96.12.dynamic.ttnet.com.tr", 6881),
                "185.21.216.140": ("", 6881),
            }, "wave", appears_after=25.0, listens=[("0.0.0.0", 6881)]),
]

DEMO_HOSTNAMES = {ip: host for app in DEMO_APPS for ip, (host, _) in app.remotes.items() if host}
DEMO_HOSTNAMES[DEMO_GATEWAY] = "router.home"


class DemoNetwork:
    """Generates plausible traffic for every demo application as time passes."""

    def __init__(self, seed: int = 7):
        self._random = random.Random(seed)
        self._started = time.monotonic()
        self._last = self._started
        self._lock = threading.Lock()
        self._pending: dict[tuple[int, str], list[int]] = {}
        self.rx_total = 3_512_000_000
        self.tx_total = 402_000_000
        self.virtual_rx = 12_000_000

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def active_apps(self) -> list[DemoApp]:
        elapsed = self.elapsed()
        return [app for app in DEMO_APPS if elapsed >= app.appears_after]

    def _factor(self, app: DemoApp, t: float) -> float:
        rnd = self._random.random
        if app.pattern == "bursty":
            return (0.15 + rnd() * 0.5) * (6 if rnd() < 0.08 else 1)
        if app.pattern == "wave":
            return max(0.0, math.sin(t / 9 + app.pid)) ** 2 * (0.7 + rnd() * 0.6)
        if app.pattern == "pulse":
            return (1.0 if int(t / 12 + app.pid) % 3 == 0 else 0.05) * (0.6 + rnd() * 0.8)
        return 0.8 + rnd() * 0.4

    def advance(self) -> None:
        with self._lock:
            now = time.monotonic()
            dt = now - self._last
            if dt < 0.05:
                return
            self._last = now
            t = now - self._started
            for app in self.active_apps():
                factor = self._factor(app, t)
                rx = int(app.down * factor * dt)
                tx = int(app.up * max(factor, 0.2) * dt)
                remotes = list(app.remotes)
                weights = [3] + [1] * (len(remotes) - 1)
                for ip in self._random.choices(remotes, weights, k=min(2, len(remotes))):
                    bucket = self._pending.setdefault((app.pid, ip), [0, 0])
                    bucket[0] += rx // 2
                    bucket[1] += tx // 2
                self.rx_total += rx
                self.tx_total += tx
            overhead = int(8_000 * dt)
            self.rx_total += overhead
            self.tx_total += overhead // 3
            self.virtual_rx += int(2_000 * dt)

    def drain(self) -> list[TrafficSample]:
        self.advance()
        with self._lock:
            pending, self._pending = self._pending, {}
        return [TrafficSample(pid, ip, rx, tx) for (pid, ip), (rx, tx) in pending.items()]


class DemoTrafficSource:
    name = "demo"

    def __init__(self, network: DemoNetwork):
        self._network = network

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def drain(self, local_addresses: set[bytes]) -> list[TrafficSample]:
        return self._network.drain()


class DemoProbe:
    def __init__(self, network: DemoNetwork):
        self._network = network
        self._processes = {app.pid: ProcessInfo(app.pid, app.name, app.exe, app.title) for app in DEMO_APPS}

    def interface_counters(self) -> dict[str, tuple[int, int]]:
        self._network.advance()
        return {
            "Wi-Fi": (self._network.rx_total, self._network.tx_total),
            "Ethernet": (0, 0),
            "vEthernet (WSL)": (self._network.virtual_rx, self._network.virtual_rx // 4),
            "Loopback Pseudo-Interface 1": (0, 0),
        }

    def interfaces(self) -> list[dict[str, Any]]:
        return [
            {"name": "Wi-Fi", "ipv4": [DEMO_LOCAL_IP], "ipv6": ["fe80::1c2b:7a4f:9e1d:3b20"],
             "mac": "3C:A9:F4:5E:12:8B", "virtual": False, "up": True, "speed_mbps": 866, "mtu": 1500},
            {"name": "Ethernet", "ipv4": [], "ipv6": [], "mac": "D8:BB:C1:07:44:A2", "virtual": False,
             "up": False, "speed_mbps": 0, "mtu": 1500},
            {"name": "vEthernet (WSL)", "ipv4": ["172.22.160.1"], "ipv6": [], "mac": "00:15:5D:A1:30:01",
             "virtual": True, "up": True, "speed_mbps": 10000, "mtu": 1500},
            {"name": "Loopback Pseudo-Interface 1", "ipv4": ["127.0.0.1"], "ipv6": ["::1"], "mac": "",
             "virtual": True, "up": True, "speed_mbps": 1073, "mtu": 1500},
        ]

    def local_addresses(self) -> set[str]:
        return {DEMO_LOCAL_IP, "127.0.0.1", "::1"}

    def connections(self) -> list[Connection]:
        result = []
        port = 51000
        for app in self._network.active_apps():
            for ip, (_, remote_port) in app.remotes.items():
                port += 7
                proto = "udp" if remote_port in (50004, 6881, 4070) else "tcp"
                result.append(Connection(proto, 4, DEMO_LOCAL_IP, port, ip, remote_port,
                                         "ESTABLISHED" if proto == "tcp" else "", app.pid))
            for address, listen_port in app.listens:
                proto = "udp" if listen_port in (5353, 5355, 57621) else "tcp"
                result.append(Connection(proto, 4, address, listen_port, "", 0,
                                         "LISTEN" if proto == "tcp" else "", app.pid))
        result.append(Connection("tcp", 4, DEMO_LOCAL_IP, 50912, "142.250.187.110", 443, "TIME_WAIT", 0))
        return result

    def process_info(self, pid: int) -> ProcessInfo:
        return self._processes.get(pid) or ProcessInfo(pid, f"PID {pid}")

    def prune_processes(self) -> None:
        pass


class DemoPinger:
    def __init__(self) -> None:
        self._random = random.Random()

    def ping(self, ip: str, timeout_ms: int = 1000) -> float | None:
        rnd = self._random.random
        time.sleep(0.02)
        if ip == DEMO_GATEWAY:
            return 1.2 + rnd() * 2.2 + (12 if rnd() < 0.02 else 0)
        if rnd() < 0.004:
            return None
        return 17 + rnd() * 7 + (45 if rnd() < 0.02 else 0)


def seed_history(storage: Storage, days: int = 75) -> None:
    rnd = random.Random(42)
    today = date.today()
    shares = {
        "chrome.exe": 0.34, "steam.exe": 0.22, "Spotify.exe": 0.05, "Discord.exe": 0.04,
        "OneDrive.exe": 0.08, "svchost.exe": 0.09, "msedge.exe": 0.03, "System": 0.01,
    }
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        weekend = day.weekday() >= 5
        total = (rnd.uniform(9, 17) if weekend else rnd.uniform(4, 10)) * 1024**3
        if offset == 0:
            total *= 0.35
        if rnd.random() < 0.08:
            total *= 2.6
        rx = int(total * 0.9)
        tx = int(total * 0.1)
        storage.add_usage(rx, tx, day)
        for name, share in shares.items():
            jitter = rnd.uniform(0.6, 1.4)
            storage.add_app_usage(name, int(rx * share * jitter), int(tx * share * jitter), day)
    for app in DEMO_APPS:
        if app.appears_after == 0:
            storage.remember_app((app.exe or app.name).lower())
        storage.remember_app_meta(app.name, app.title, app.exe)
    storage.update_settings({"quota_gb": 400, "quota_reset_day": 1})
    for index in range(8):
        storage.add_speedtest({
            "ok": True,
            "ts": time.time() - (8 - index) * 86400 * 3,
            "ping_ms": rnd.uniform(14, 24),
            "jitter_ms": rnd.uniform(1, 4),
            "download_bps": rnd.uniform(82, 97) * 1e6,
            "upload_bps": rnd.uniform(18, 24) * 1e6,
            "server": {"colo": "IST", "city": "Istanbul", "isp": "Demo Telekom"},
        })


class DemoSpeedTest:
    """Replays a plausible speed test so the tools page can be previewed offline."""

    def __init__(self, on_progress, duration: float = 4.0):
        self._on_progress = on_progress
        self._duration = duration
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _phase(self, name: str, target_bps: float, state: dict[str, Any]) -> float | None:
        steps = int(self._duration / 0.25)
        for step in range(steps):
            if self._cancel.wait(0.25):
                return None
            ramp = min(1.0, (step + 1) / (steps * 0.3))
            state.update(phase=name, current_bps=target_bps * ramp * random.uniform(0.93, 1.05),
                         progress=(step + 1) / steps)
            self._on_progress(dict(state))
        return target_bps * random.uniform(0.95, 1.02)

    def run(self) -> dict[str, Any]:
        server = {"colo": "IST", "city": "Istanbul", "isp": "Demo Telekom", "country": "TR"}
        state: dict[str, Any] = {"phase": "latency", "progress": 0.0}
        self._on_progress(dict(state))
        time.sleep(0.8)
        state.update(ping_ms=18.4, jitter_ms=2.1, server=server)
        download = self._phase("download", 94e6, state)
        state["download_bps"] = download
        upload = self._phase("upload", 21e6, state) if download else None
        result = {
            "ok": bool(download and upload), "ts": time.time(), "ping_ms": 18.4, "jitter_ms": 2.1,
            "download_bps": download, "upload_bps": upload, "server": server,
        }
        state.update(phase="done", upload_bps=upload, progress=1.0, result=result)
        self._on_progress(dict(state))
        return result


DEMO_ROUTE = [
    ("192.168.1.1", "router.home", 1.4),
    ("10.34.0.1", "", 6.8),
    ("81.212.104.13", "81.212.104.13.static.ttnet.com.tr", 8.9),
    ("212.156.101.161", "", 11.2),
    ("72.14.218.108", "", 17.5),
    ("142.251.51.227", "", 18.1),
    ("142.250.187.110", "fra24s06-in-f14.1e100.net", 18.6),
]


def demo_trace(host: str):
    for ttl, (ip, name, base) in enumerate(DEMO_ROUTE, start=1):
        time.sleep(0.35)
        rtts = [None, None, None] if ttl == 2 else [round(base + random.uniform(-0.6, 1.8), 1) for _ in range(3)]
        yield {"ttl": ttl, "ip": None if ttl == 2 else ip, "rtts": rtts, "host": name}
