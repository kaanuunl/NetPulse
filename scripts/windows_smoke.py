"""End-to-end checks that need a real Windows machine (run in CI as administrator).

Every Win32 binding is exercised against the live OS: the ETW consumer must
attribute a real download to this process, a firewall rule must appear and
disappear, ICMP, routing, icons, the tray icon and autostart must work, and
the packaged application must serve its API.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agnabzi.system import is_admin  # noqa: E402
from agnabzi.traffic import pack_ip  # noqa: E402

DOWNLOAD_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
RESULTS: dict[str, dict] = {}
CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, required: bool = True):
    def decorator(fn):
        CHECKS.append((name, required, fn))
        return fn

    return decorator


def run_check(name: str, required: bool, fn) -> None:
    started = time.perf_counter()
    try:
        RESULTS[name] = {"ok": True, "required": required, "detail": fn()}
    except Exception as exc:
        RESULTS[name] = {"ok": False, "required": required, "error": repr(exc), "trace": traceback.format_exc()}
    RESULTS[name]["seconds"] = round(time.perf_counter() - started, 2)


def download(limit: int) -> tuple[int, set[str]]:
    """Read ``limit`` bytes from a large public file; return the count and the server addresses."""
    host = DOWNLOAD_URL.split("/")[2]
    addresses = {info[4][0] for info in socket.getaddrinfo(host, 443)}
    request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "NetPulse-CI"})
    received = 0
    with urllib.request.urlopen(request, timeout=30) as response:
        while received < limit:
            chunk = response.read(min(65536, limit - received))
            if not chunk:
                break
            received += len(chunk)
    return received, addresses


def local_addresses() -> set[bytes]:
    import psutil

    found = set()
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family in (socket.AF_INET, socket.AF_INET6):
                packed = pack_ip(addr.address)
                if packed:
                    found.add(packed)
    return found


@check("etw_attributes_download_to_process")
def _etw():
    from agnabzi.windows.etw import EtwTrafficSource

    source = EtwTrafficSource(session_name="NetPulse-CI")
    source.start()
    try:
        time.sleep(1.5)
        downloaded, server_ips = download(3_000_000)
        time.sleep(2.5)
        samples = source.drain(local_addresses())
    finally:
        source.stop()
    mine = [s for s in samples if s.pid == os.getpid()]
    received = sum(s.rx for s in mine)
    remotes = sorted({s.remote_ip for s in mine})
    assert downloaded == 3_000_000
    assert received >= 2_500_000, f"only {received} bytes attributed to this process; samples={samples[:10]}"
    assert server_ips & set(remotes), f"remote ips {remotes} do not include {server_ips}"
    return {"events": source.events_seen, "received": received, "remotes": remotes}


@check("firewall_rule_roundtrip")
def _firewall():
    from agnabzi.windows import firewall

    # netsh needs an existing executable addressed by its long path, like the ones
    # psutil reports (%TEMP% on the runners is an 8.3 short path), and spaces must survive.
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "Firewall Test")
    os.makedirs(folder, exist_ok=True)
    exe = os.path.join(folder, "agnabzi ci dummy.exe")
    shutil.copyfile(sys.executable, exe)
    name = firewall.rule_name(exe)
    firewall.block(exe, sys.executable)
    present = firewall.rule_exists(name)
    firewall.unblock(exe)
    gone = not firewall.rule_exists(name)
    assert present and gone, (present, gone)
    return name


@check("icmp_and_traceroute")
def _icmp():
    from agnabzi import traceroute
    from agnabzi.windows.api import IcmpPinger

    pinger = IcmpPinger()
    result = pinger.echo("127.0.0.1", 1000)
    assert result.status == "ok", result
    hops = list(traceroute.trace("127.0.0.1", max_hops=3))
    assert hops and hops[-1]["ip"] == "127.0.0.1", hops
    return {"loopback_ms": result.rtt_ms, "hops": hops}


@check("gateway_lookup", required=False)
def _gateway():
    from agnabzi.windows.api import best_route_gateway

    return best_route_gateway()


@check("version_resource_and_icon")
def _resources():
    from agnabzi.windows.api import file_description
    from agnabzi.windows.icons import IconCache

    description = file_description(sys.executable)
    assert "python" in description.lower(), description
    cache = IconCache()
    try:
        png = cache.get(sys.executable)
    finally:
        cache.shutdown()
    assert png and png.startswith(b"\x89PNG"), "no icon"
    return {"description": description, "icon_bytes": len(png)}


@check("process_probe")
def _probe():
    from agnabzi.probe import SystemProbe
    from agnabzi.windows.api import file_description

    probe = SystemProbe(describe_executable=file_description)
    me = probe.process_info(os.getpid())
    assert me.exe.lower() == sys.executable.lower(), me
    assert probe.interface_counters()
    connections = probe.connections()
    return {"process": me.display_name, "connections": len(connections)}


@check("tray_icon", required=False)
def _tray():
    from agnabzi.windows.tray import TrayIcon

    tray = TrayIcon("NetPulse CI", None, lambda: [], lambda: None, lambda: None)
    started = tray.start()
    tray.set_tooltip("NetPulse\n1 Mbit/s")
    tray.notify("NetPulse", "CI balloon")
    time.sleep(0.5)
    tray.stop()
    return {"started": started}


@check("autostart_roundtrip")
def _autostart():
    from agnabzi.windows import autostart

    executable = sys.executable
    modes = {}
    for elevated in (False, True):
        mode = autostart.enable(executable, ["--background"], elevated=elevated)
        modes[str(elevated)] = (mode, autostart.status())
        autostart.disable()
    assert modes["False"][0] == "registry" and modes["True"][0] == "task", modes
    assert autostart.status() == {"enabled": False, "mode": None}
    return modes


@check("dns_over_https", required=False)
def _dns_over_https():
    from agnabzi.netinfo import dns_tampering_check

    result = dns_tampering_check("cloudflare.com")
    assert result["doh"], f"DoH returned no answers: {result}"
    assert result["verdict"] in ("ok", "tampered", "blocked"), result
    return result


@check("dpi_detection")
def _dpi_detection():
    from agnabzi.platforms import WindowsPlatform

    status = WindowsPlatform().dpi_status()
    assert status["supported"] is True, status
    return status


@check("speedtest_endpoints", required=False)
def _speedtest_endpoints():
    variants = {
        "agent": {"User-Agent": "NetPulse"},
        "agent_referer": {"User-Agent": "NetPulse", "Referer": "https://speed.cloudflare.com/"},
        "none": {},
    }
    outcome = {}
    for label, headers in variants.items():
        for path in ("/__down?bytes=100000", "/meta"):
            conn = http.client.HTTPSConnection("speed.cloudflare.com", timeout=15)
            try:
                conn.request("GET", path, headers=headers)
                response = conn.getresponse()
                response.read()
                outcome[f"{label} {path}"] = response.status
            except OSError as exc:
                outcome[f"{label} {path}"] = repr(exc)
            finally:
                conn.close()
    return outcome


@check("speedtest_live", required=False)
def _speedtest_live():
    from agnabzi.speedtest import SpeedTest

    result = SpeedTest(duration=3.0).run()
    assert result.get("ok"), result
    return result


def _get(port: int, path: str, token: str | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"X-NetPulse-Token": token} if token else {})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


@check("application_end_to_end")
def _application():
    port = 8799
    home = tempfile.mkdtemp(prefix="agnabzi-ci-")
    env = dict(os.environ, NETPULSE_HOME=home)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    process = subprocess.Popen(
        [sys.executable, os.path.join(root, "run.py"), "--console", "--no-browser", "--port", str(port)],
        env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(40):
            try:
                if _get(port, "/api/ping")[0] == 200:
                    break
            except OSError:
                time.sleep(0.5)
        status, page = _get(port, "/")
        token = re.search(rb'name="netpulse-token" content="([^"]+)"', page).group(1).decode()
        download(2_000_000)
        time.sleep(4)
        overview = json.loads(_get(port, "/api/overview", token)[1])
        apps = json.loads(_get(port, "/api/apps", token)[1])
        security = json.loads(_get(port, "/api/security", token)[1])
        assert overview["caps"]["per_app"], overview["caps"]
        assert overview["session"]["rx"] > 0, overview["session"]
        me = [a for a in apps["apps"] if os.getpid() in a["pids"]]
        assert me and me[0]["rx_total"] >= 1_500_000, me
        assert "summary" in security["threats"] and security["posture"]["firewall"], security["posture"]
        return {
            "caps": overview["caps"], "session": overview["session"], "self": me[0]["rx_total"],
            "threats": security["threats"]["summary"], "dpi": security["posture"]["dpi"],
        }
    finally:
        process.terminate()
        try:
            output = process.communicate(timeout=15)[0].decode(errors="replace")
        except subprocess.TimeoutExpired:
            process.kill()
            output = ""
        RESULTS.setdefault("application_output", {"ok": True, "required": False, "detail": output[-3000:]})


def main() -> int:
    if not is_admin():
        print("This smoke test must run as administrator.")
        return 2
    for name, required, fn in CHECKS:
        run_check(name, required, fn)
    print(json.dumps(RESULTS, indent=2, default=str))
    failed = [name for name, result in RESULTS.items() if result["required"] and not result["ok"]]
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("All Windows smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
