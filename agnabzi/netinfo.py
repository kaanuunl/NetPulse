"""Small network helpers: address classification, name resolution, gateway lookup."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

from agnabzi.system import IS_WINDOWS

log = logging.getLogger(__name__)

_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_.:\-]*[A-Za-z0-9_.])?$")

WELL_KNOWN_PORTS = {
    20: "FTP-DATA", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP",
    68: "DHCP", 69: "TFTP", 80: "HTTP", 110: "POP3", 123: "NTP", 135: "RPC", 137: "NetBIOS",
    138: "NetBIOS", 139: "NetBIOS", 143: "IMAP", 161: "SNMP", 389: "LDAP", 443: "HTTPS",
    445: "SMB", 465: "SMTPS", 500: "IKE", 514: "Syslog", 587: "SMTP", 636: "LDAPS",
    853: "DNS-over-TLS", 993: "IMAPS", 995: "POP3S", 1080: "SOCKS", 1194: "OpenVPN",
    1433: "MSSQL", 1701: "L2TP", 1723: "PPTP", 1900: "SSDP", 3074: "Xbox Live",
    3306: "MySQL", 3389: "RDP", 3478: "STUN", 3479: "STUN", 4500: "IPsec NAT-T",
    5060: "SIP", 5222: "XMPP", 5228: "Google Play", 5353: "mDNS", 5355: "LLMNR",
    5432: "PostgreSQL", 5900: "VNC", 6379: "Redis", 6881: "BitTorrent", 7680: "Delivery Optimization",
    8080: "HTTP-Alt", 8443: "HTTPS-Alt", 9993: "ZeroTier", 25565: "Minecraft",
    27015: "Steam", 27036: "Steam", 41641: "Tailscale", 51820: "WireGuard", 57621: "Spotify",
}


def is_valid_host(value: str) -> bool:
    return bool(value) and not value.startswith("-") and bool(_HOST_RE.match(value))


def service_name(port: int) -> str:
    return WELL_KNOWN_PORTS.get(port, "")


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def classify_ip(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return "loopback"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_multicast or str(addr) == "255.255.255.255":
        return "multicast"
    if addr.is_link_local:
        return "link-local"
    if addr.is_private or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT):
        return "private"
    return "public"


def is_loopback(ip: str) -> bool:
    return classify_ip(ip) in ("loopback", "unspecified")


def is_internet(ip: str) -> bool:
    return classify_ip(ip) == "public"


class ReverseDns:
    """Asynchronous, cached reverse lookups so the UI never waits on DNS."""

    def __init__(self, ttl: float = 3600.0, workers: int = 4, static: dict[str, str] | None = None):
        self._ttl = ttl
        self._cache: dict[str, tuple[str, float]] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rdns")
        self._static = static or {}

    def lookup(self, ip: str) -> str:
        if ip in self._static:
            return self._static[ip]
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(ip)
            if cached and now - cached[1] < self._ttl:
                return cached[0]
            if ip in self._pending or classify_ip(ip) in ("loopback", "unspecified", "multicast"):
                return cached[0] if cached else ""
            self._pending.add(ip)
        self._pool.submit(self._resolve, ip)
        return cached[0] if cached else ""

    def resolve_now(self, ip: str, timeout: float = 2.0) -> str:
        future = self._pool.submit(self._resolve, ip)
        try:
            return future.result(timeout)
        except Exception:
            return ""

    def _resolve(self, ip: str) -> str:
        try:
            name = socket.gethostbyaddr(ip)[0]
        except (OSError, UnicodeError):
            name = ""
        with self._lock:
            self._cache[ip] = (name, time.monotonic())
            self._pending.discard(ip)
        return name

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def parse_proc_net_route(text: str) -> str | None:
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[1] != "00000000":
            continue
        flags = int(fields[3], 16)
        if not flags & 0x2:
            continue
        return socket.inet_ntoa(int(fields[2], 16).to_bytes(4, "little"))
    return None


def default_gateway() -> str | None:
    try:
        if IS_WINDOWS:
            from agnabzi.windows import api

            return api.best_route_gateway()
        try:
            with open("/proc/net/route", encoding="ascii") as fh:
                return parse_proc_net_route(fh.read())
        except OSError:
            output = subprocess.run(
                ["route", "-n", "get", "default"], capture_output=True, text=True, timeout=3
            ).stdout
            match = re.search(r"gateway:\s*(\S+)", output)
            return match.group(1) if match else None
    except Exception as exc:
        log.debug("gateway lookup failed: %s", exc)
        return None


def resolve_ipv4(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except (OSError, UnicodeError):
        return None


def dns_lookup(name: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        infos = socket.getaddrinfo(name, None)
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "error": str(exc), "ms": (time.perf_counter() - started) * 1000}
    elapsed = (time.perf_counter() - started) * 1000
    seen: list[str] = []
    for *_unused, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in seen:
            seen.append(ip)
    return {
        "ok": True,
        "ms": elapsed,
        "ipv4": [ip for ip in seen if ":" not in ip],
        "ipv6": [ip for ip in seen if ":" in ip],
    }


def port_check(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            peer = sock.getpeername()[0]
        return {"state": "open", "ms": (time.perf_counter() - started) * 1000, "ip": peer}
    except ConnectionRefusedError:
        return {"state": "closed", "ms": (time.perf_counter() - started) * 1000}
    except socket.timeout:
        return {"state": "filtered", "ms": timeout * 1000}
    except OSError as exc:
        return {"state": "error", "error": str(exc)}


def _fetch_json(url: str, timeout: float = 6.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "NetPulse", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return json.loads(response.read(64 * 1024).decode("utf-8"))


DOH_SERVERS = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
}


def doh_lookup(name: str, server: str = "cloudflare", timeout: float = 6.0) -> list[str]:
    """Resolve ``name`` to IPv4 addresses over DNS-over-HTTPS (encrypted, tamper-resistant)."""
    endpoint = DOH_SERVERS.get(server, DOH_SERVERS["cloudflare"])
    url = f"{endpoint}?name={quote(name)}&type=A"
    request = urllib.request.Request(url, headers={"accept": "application/dns-json", "User-Agent": "NetPulse"})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        data = json.loads(response.read(64 * 1024).decode("utf-8"))
    return [answer["data"] for answer in data.get("Answer", []) if answer.get("type") == 1]


def dns_verdict(system: list[str], doh: list[str], doh_ok: bool) -> str:
    """Classify a comparison between the system resolver and DoH answers.

    ``tampered`` is only reported on unmistakable signals — the system resolver
    hands back a private, loopback or unspecified address (a block page or
    sinkhole) while DoH sees a real one, or the system returns nothing while DoH
    does. Two different sets of public addresses are usually just a CDN, so that
    stays ``ok``.
    """
    if not doh_ok:
        return "doh_failed"
    doh_public = {ip for ip in doh if classify_ip(ip) == "public"}
    system_public = {ip for ip in system if classify_ip(ip) == "public"}
    system_nonpublic = [ip for ip in system if classify_ip(ip) in ("private", "loopback", "unspecified")]
    if doh_public and system_nonpublic and not system_public:
        return "tampered"
    if doh_public and not system:
        return "blocked"
    return "ok"


def dns_tampering_check(name: str) -> dict[str, Any]:
    """Compare the system resolver with encrypted DNS to reveal ISP-side blocking."""
    try:
        system = sorted({info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)})
    except (OSError, UnicodeError):
        system = []
    doh_ok = True
    try:
        doh = sorted(set(doh_lookup(name)))
    except Exception as exc:
        doh, doh_ok = [], False
        log.debug("DoH lookup failed for %s: %s", name, exc)
    return {
        "ok": bool(system) or doh_ok,
        "name": name,
        "system": system,
        "doh": doh,
        "verdict": dns_verdict(system, doh, doh_ok),
        "resolver": "Cloudflare (DoH)",
    }


_ip_info_cache: dict[str, tuple[dict[str, Any], float]] = {}


def ip_info(ip: str | None = None) -> dict[str, Any]:
    """Public IP details (own address when ``ip`` is None) from ipinfo.io."""
    cache_key = ip or "self"
    cached = _ip_info_cache.get(cache_key)
    if cached and time.monotonic() - cached[1] < 600:
        return cached[0]
    url = f"https://ipinfo.io/{ip}/json" if ip else "https://ipinfo.io/json"
    try:
        data = _fetch_json(url)
        result = {
            "ok": True,
            "ip": data.get("ip", ip or ""),
            "hostname": data.get("hostname", ""),
            "city": data.get("city", ""),
            "region": data.get("region", ""),
            "country": data.get("country", ""),
            "org": data.get("org", ""),
            "timezone": data.get("timezone", ""),
        }
    except Exception as exc:
        if ip:
            return {"ok": False, "error": str(exc)}
        try:
            result = {"ok": True, "ip": _fetch_json("https://api.ipify.org?format=json")["ip"]}
        except Exception as fallback_exc:
            return {"ok": False, "error": str(fallback_exc)}
    _ip_info_cache[cache_key] = (result, time.monotonic())
    return result
