import time
from datetime import date

import pytest

from agnabzi.collector import Collector
from agnabzi.events import EventLog
from agnabzi.models import Connection, ProcessInfo
from agnabzi.netinfo import ReverseDns
from agnabzi.storage import Storage
from agnabzi.traffic import TrafficSample

T0 = time.time()

PROCESSES = {
    10: ProcessInfo(10, "chrome.exe", r"C:\Apps\chrome.exe", "Google Chrome"),
    11: ProcessInfo(11, "chrome.exe", r"C:\Apps\chrome.exe", "Google Chrome"),
    20: ProcessInfo(20, "game.exe", r"D:\Games\game.exe", ""),
}


class FakeProbe:
    def __init__(self):
        self.counters = {"Wi-Fi": (0, 0), "vEthernet (WSL)": (0, 0)}
        self.conns = []

    def interface_counters(self):
        return dict(self.counters)

    def interfaces(self):
        return [{"name": n, "ipv4": [], "ipv6": [], "mac": "", "virtual": "vEthernet" in n, "up": True,
                 "speed_mbps": 0, "mtu": 1500} for n in self.counters]

    def local_addresses(self):
        return {"192.168.1.5"}

    def connections(self):
        return list(self.conns)

    def process_info(self, pid):
        return PROCESSES.get(pid, ProcessInfo(pid, f"PID {pid}"))

    def prune_processes(self):
        pass


class FakeTraffic:
    name = "fake"

    def __init__(self):
        self.pending = []

    def start(self):
        pass

    def stop(self):
        pass

    def drain(self, local):
        pending, self.pending = self.pending, []
        return pending


def tcp(pid, remote, port=443, status="ESTABLISHED"):
    return Connection("tcp", 4, "192.168.1.5", 50000 + pid, remote, port, status, pid)


@pytest.fixture
def setup():
    probe = FakeProbe()
    traffic = FakeTraffic()
    storage = Storage.in_memory()
    events = EventLog()
    rdns = ReverseDns(static={"93.184.216.34": "example.com"})
    collector = Collector(probe, storage, events, rdns, traffic=traffic, connection_interval=0)
    yield probe, traffic, storage, events, collector
    rdns.shutdown()


def test_rates_and_totals_skip_virtual_adapters(setup):
    probe, _, storage, _, collector = setup
    collector.tick(now=T0 + 1000.0)
    probe.counters = {"Wi-Fi": (2_000_000, 100_000), "vEthernet (WSL)": (9_000_000, 9_000_000)}
    collector.tick(now=T0 + 1002.0)
    assert collector.rate_down == pytest.approx(1_000_000)
    assert collector.rate_up == pytest.approx(50_000)
    assert collector.session_rx == 2_000_000
    assert storage.usage_summary()["today"] == {"rx": 2_000_000, "tx": 100_000}
    assert collector.adapter_rates["vEthernet (WSL)"][0] == pytest.approx(4_500_000)


def test_counter_reset_does_not_go_negative(setup):
    probe, _, _, _, collector = setup
    probe.counters = {"Wi-Fi": (5_000, 5_000)}
    collector.tick(now=T0 + 1.0)
    probe.counters = {"Wi-Fi": (100, 100)}
    collector.tick(now=T0 + 2.0)
    assert collector.rate_down == 0 and collector.session_rx == 0


def test_explicit_adapter_selection(setup):
    probe, _, storage, _, collector = setup
    storage.update_settings({"excluded_adapters": ["Wi-Fi"]})
    collector.tick(now=T0 + 1.0)
    probe.counters = {"Wi-Fi": (1000, 0), "vEthernet (WSL)": (300, 0)}
    collector.tick(now=T0 + 2.0)
    assert collector.session_rx == 300


def test_per_app_accounting_groups_processes_and_ignores_loopback(setup):
    probe, traffic, storage, _, collector = setup
    collector.tick(now=T0 + 1.0)
    traffic.pending = [
        TrafficSample(10, "93.184.216.34", 4000, 400),
        TrafficSample(11, "93.184.216.34", 1000, 100),
        TrafficSample(20, "127.0.0.1", 99999, 99999),
        TrafficSample(20, "51.1.1.1", 200, 20),
    ]
    collector.tick(now=T0 + 2.0)
    apps = {app["name"]: app for app in collector.apps_snapshot()}
    chrome = apps["chrome.exe"]
    assert chrome["rx_total"] == 5000 and chrome["tx_total"] == 500
    assert chrome["pids"] == [10, 11]
    assert chrome["remotes"][0]["host"] == "example.com"
    assert apps["game.exe"]["rx_total"] == 200
    today = date.today()
    top = storage.top_apps(today, today)
    assert top[0]["app"] == "chrome.exe" and top[0]["title"] == "Google Chrome"


def test_new_app_alerts_after_baseline(setup):
    probe, _, _, events, collector = setup
    probe.conns = [tcp(10, "93.184.216.34")]
    collector.tick(now=T0 + 1.0)
    kinds = [e["kind"] for e in events.since(0)]
    assert kinds == ["baseline"]

    probe.conns.append(tcp(20, "51.1.1.1", 27015))
    collector.tick(now=T0 + 2.0)
    new_apps = [e for e in events.since(0) if e["kind"] == "new_app"]
    assert len(new_apps) == 1
    assert new_apps[0]["data"]["name"] == "game.exe"

    collector.tick(now=T0 + 3.0)
    assert len([e for e in events.since(0) if e["kind"] == "new_app"]) == 1


def test_local_only_connections_do_not_trigger_alerts(setup):
    probe, _, storage, events, collector = setup
    storage.remember_app("seed")
    collector._baseline_done = True
    probe.conns = [tcp(20, "192.168.1.20", 445), tcp(20, "127.0.0.1", 8080)]
    collector.tick(now=T0 + 1.0)
    assert not events.since(0)
    app = collector.app_by_key(r"d:\games\game.exe")
    assert app.connections == 1


def test_quota_event(setup):
    _, _, storage, events, collector = setup
    storage.update_settings({"quota_gb": 1})
    storage.add_usage(900 * 1024**2, 0)
    collector.tick(now=T0 + 100.0)
    quota = [e for e in events.since(0) if e["kind"] == "quota"]
    assert len(quota) == 1 and quota[0]["data"]["threshold"] == 80


def test_overview_shape(setup):
    probe, _, _, _, collector = setup
    probe.conns = [tcp(10, "93.184.216.34"), Connection("tcp", 4, "0.0.0.0", 445, "", 0, "LISTEN", 20)]
    collector.tick(now=T0 + 1.0)
    collector.tick(now=T0 + 2.0)
    overview = collector.overview()
    assert overview["counts"] == {"apps": 1, "connections": 1, "listening": 1, "threats": 1}
    assert {"rates", "history", "session", "usage", "top_apps"} <= set(overview)
    rows = collector.connections_snapshot()
    listening = [r for r in rows if r["listening"]][0]
    assert listening["listen_scope"] == "unspecified" and listening["service"] == "SMB"
