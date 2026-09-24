"""Hop-by-hop route discovery."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator

from agnabzi.netinfo import resolve_ipv4
from agnabzi.system import IS_WINDOWS

MAX_HOPS = 30
PROBES_PER_HOP = 3

_HOP_LINE = re.compile(r"^\s*(\d+)\s+(.*)$")
_HOP_ADDRESS = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")
_HOP_RTT = re.compile(r"([\d.]+)\s*ms")


class TracerouteUnavailable(RuntimeError):
    pass


def _icmp_hops(ip: str, max_hops: int, timeout_ms: int) -> Iterator[dict]:
    from agnabzi.windows.api import IcmpPinger

    pinger = IcmpPinger()
    try:
        for ttl in range(1, max_hops + 1):
            responder, rtts, reached = None, [], False
            for _ in range(PROBES_PER_HOP):
                result = pinger.echo(ip, timeout_ms, ttl=ttl)
                rtts.append(result.rtt_ms if result.status in ("ok", "ttl_expired", "unreachable") else None)
                responder = responder or result.address
                reached = reached or result.status in ("ok", "unreachable")
            yield {"ttl": ttl, "ip": responder, "rtts": rtts}
            if reached:
                return
    finally:
        pinger.close()


def parse_traceroute_line(line: str) -> dict | None:
    match = _HOP_LINE.match(line)
    if not match:
        return None
    body = match.group(2)
    address = _HOP_ADDRESS.search(body)
    rtts: list[float | None] = [float(v) for v in _HOP_RTT.findall(body)]
    rtts += [None] * body.count("*")
    return {"ttl": int(match.group(1)), "ip": address.group(1) if address else None, "rtts": rtts[:PROBES_PER_HOP]}


def _subprocess_hops(ip: str, max_hops: int, timeout_ms: int) -> Iterator[dict]:
    binary = shutil.which("traceroute")
    if not binary:
        raise TracerouteUnavailable("traceroute")
    wait = str(max(1, timeout_ms // 1000))
    command = [binary, "-n", "-q", str(PROBES_PER_HOP), "-w", wait, "-m", str(max_hops), ip]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as process:
        assert process.stdout is not None
        for line in process.stdout:
            hop = parse_traceroute_line(line)
            if hop:
                yield hop


def trace(host: str, max_hops: int = MAX_HOPS, timeout_ms: int = 1000) -> Iterator[dict]:
    """Yield one dict per hop: ``{"ttl", "ip", "rtts"}``."""
    ip = resolve_ipv4(host)
    if ip is None:
        raise LookupError(host)
    hops = _icmp_hops if IS_WINDOWS else _subprocess_hops
    yield from hops(ip, max_hops, timeout_ms)
