"""Per-process traffic accounting shared by every traffic source."""

from __future__ import annotations

import socket
import struct
import threading
from typing import NamedTuple, Protocol


class TrafficSample(NamedTuple):
    pid: int
    remote_ip: str
    rx: int
    tx: int


class TrafficSource(Protocol):
    name: str

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def drain(self, local_addresses: set[bytes]) -> list[TrafficSample]: ...


# Microsoft-Windows-Kernel-Network event ids -> (is_send, is_ipv6).
KERNEL_NETWORK_EVENTS = {
    10: (True, False),   # TCP data sent, IPv4
    11: (False, False),  # TCP data received, IPv4
    26: (True, True),    # TCP data sent, IPv6
    27: (False, True),   # TCP data received, IPv6
    42: (True, False),   # UDP data sent, IPv4
    43: (False, False),  # UDP data received, IPv4
    58: (True, True),    # UDP data sent, IPv6
    59: (False, True),   # UDP data received, IPv6
}


class KernelNetworkPacket(NamedTuple):
    pid: int
    size: int
    sent: bool
    daddr: bytes
    saddr: bytes


def parse_kernel_network(event_id: int, payload: bytes) -> KernelNetworkPacket | None:
    """Decode the fixed prefix shared by every Kernel-Network data event.

    Layout: PID (u32), size (u32), daddr, saddr, dport (u16 BE), sport (u16 BE), ...
    Addresses are 4 bytes for IPv4 events and 16 bytes for IPv6 events.
    """
    kind = KERNEL_NETWORK_EVENTS.get(event_id)
    if kind is None:
        return None
    sent, ipv6 = kind
    width = 16 if ipv6 else 4
    if len(payload) < 8 + 2 * width:
        return None
    pid, size = struct.unpack_from("<II", payload)
    daddr = payload[8 : 8 + width]
    saddr = payload[8 + width : 8 + 2 * width]
    return KernelNetworkPacket(pid, size, sent, daddr, saddr)


def pack_ip(ip: str) -> bytes | None:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        return socket.inet_pton(family, ip.split("%", 1)[0])
    except OSError:
        return None


def unpack_ip(raw: bytes) -> str:
    if len(raw) == 16:
        if raw[:12] == b"\x00" * 10 + b"\xff\xff":
            return socket.inet_ntop(socket.AF_INET, raw[12:])
        return socket.inet_ntop(socket.AF_INET6, raw)
    return socket.inet_ntop(socket.AF_INET, raw)


class FlowAccumulator:
    """Collects byte counts per (pid, address pair) between two drains."""

    def __init__(self) -> None:
        self._flows: dict[tuple[int, bytes, bytes], list[int]] = {}
        self._lock = threading.Lock()

    def add(self, pid: int, daddr: bytes, saddr: bytes, size: int, sent: bool) -> None:
        key = (pid, daddr, saddr)
        with self._lock:
            counters = self._flows.get(key)
            if counters is None:
                counters = self._flows[key] = [0, 0]
            counters[1 if sent else 0] += size

    def drain(self, local_addresses: set[bytes]) -> list[TrafficSample]:
        with self._lock:
            flows, self._flows = self._flows, {}
        merged: dict[tuple[int, str], list[int]] = {}
        for (pid, daddr, saddr), (rx, tx) in flows.items():
            remote = saddr if daddr in local_addresses and saddr not in local_addresses else daddr
            counters = merged.setdefault((pid, unpack_ip(remote)), [0, 0])
            counters[0] += rx
            counters[1] += tx
        return [TrafficSample(pid, ip, rx, tx) for (pid, ip), (rx, tx) in merged.items()]
