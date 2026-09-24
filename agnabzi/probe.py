"""Snapshots of the operating system's interfaces, sockets and processes."""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable

import psutil

from agnabzi.models import Connection, ProcessInfo

_VIRTUAL_PREFIXES = (
    "veth", "br-", "virbr", "docker", "tun", "tap", "wg", "utun", "awdl", "llw", "bridge",
    "vmnet", "vboxnet", "tailscale", "zt",
)
_VIRTUAL_MARKERS = (
    "loopback", "vethernet", "virtualbox", "vmware", "hyper-v", "pseudo", "tap-windows", "wireguard",
    "openvpn", "tailscale", "zerotier", "npcap", "wintun", "nordlynx", "wsl", "teredo", "isatap",
    "bluetooth",
)
_PROCESS_RECHECK = 30.0
_PROCESS_EXPIRY = 600.0


def is_virtual_adapter(name: str) -> bool:
    lower = name.lower()
    return lower in ("lo", "lo0") or lower.startswith(_VIRTUAL_PREFIXES) or any(m in lower for m in _VIRTUAL_MARKERS)


def _address(addr: Any) -> tuple[str, int]:
    if not addr:
        return "", 0
    return addr.ip, addr.port


class SystemProbe:
    def __init__(self, describe_executable: Callable[[str], str] | None = None):
        self._describe = describe_executable
        self._descriptions: dict[str, str] = {}
        self._processes: dict[int, tuple[ProcessInfo, float, float]] = {}
        self._lock = threading.Lock()

    def interface_counters(self) -> dict[str, tuple[int, int]]:
        counters = psutil.net_io_counters(pernic=True)
        return {name: (c.bytes_recv, c.bytes_sent) for name, c in counters.items()}

    def interfaces(self) -> list[dict[str, Any]]:
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        result = []
        for name in sorted(set(addresses) | set(stats), key=str.lower):
            entry: dict[str, Any] = {
                "name": name, "ipv4": [], "ipv6": [], "mac": "", "virtual": is_virtual_adapter(name),
            }
            for addr in addresses.get(name, []):
                if addr.family == socket.AF_INET:
                    entry["ipv4"].append(addr.address)
                elif addr.family == socket.AF_INET6:
                    entry["ipv6"].append(addr.address.split("%", 1)[0])
                elif addr.family == psutil.AF_LINK and addr.address:
                    entry["mac"] = addr.address.replace("-", ":").upper()
            stat = stats.get(name)
            entry.update(
                up=bool(stat and stat.isup),
                speed_mbps=stat.speed if stat else 0,
                mtu=stat.mtu if stat else 0,
            )
            result.append(entry)
        return result

    def local_addresses(self) -> set[str]:
        found = set()
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family in (socket.AF_INET, socket.AF_INET6):
                    found.add(addr.address.split("%", 1)[0])
        return found

    def connections(self) -> list[Connection]:
        try:
            raw = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, OSError):
            return []
        result = []
        for conn in raw:
            local_ip, local_port = _address(conn.laddr)
            remote_ip, remote_port = _address(conn.raddr)
            result.append(
                Connection(
                    proto="tcp" if conn.type == socket.SOCK_STREAM else "udp",
                    family=6 if conn.family == socket.AF_INET6 else 4,
                    local_ip=local_ip,
                    local_port=local_port,
                    remote_ip=remote_ip,
                    remote_port=remote_port,
                    status=conn.status if conn.status != psutil.CONN_NONE else "",
                    pid=conn.pid or 0,
                )
            )
        return result

    def _description(self, exe: str) -> str:
        if not exe or self._describe is None:
            return ""
        cached = self._descriptions.get(exe)
        if cached is None:
            try:
                cached = self._describe(exe)
            except OSError:
                cached = ""
            self._descriptions[exe] = cached
        return cached

    def process_info(self, pid: int) -> ProcessInfo:
        now = time.monotonic()
        with self._lock:
            cached = self._processes.get(pid)
        if cached and now - cached[2] < _PROCESS_RECHECK:
            return cached[0]
        try:
            process = psutil.Process(pid)
            created = process.create_time()
            if cached and cached[1] == created:
                info = cached[0]
            else:
                with process.oneshot():
                    name = process.name()
                    try:
                        exe = process.exe()
                    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                        exe = ""
                info = ProcessInfo(pid, name or f"PID {pid}", exe, self._description(exe))
        except psutil.NoSuchProcess:
            if cached:
                return cached[0]
            info, created = ProcessInfo(pid, f"PID {pid}"), 0.0
        except (psutil.AccessDenied, OSError):
            info, created = ProcessInfo(pid, f"PID {pid}"), 0.0
        with self._lock:
            self._processes[pid] = (info, created, now)
        return info

    def prune_processes(self) -> None:
        cutoff = time.monotonic() - _PROCESS_EXPIRY
        with self._lock:
            for pid in [pid for pid, (_, _, checked) in self._processes.items() if checked < cutoff]:
                del self._processes[pid]
