import pytest

from agnabzi.threats import AppActivity, analyze, location_category, location_risk, summarize


@pytest.mark.parametrize("exe, risk", [
    (r"C:\Users\Me\AppData\Local\Temp\x.exe", "high"),
    (r"C:\Windows\Temp\y.exe", "high"),
    (r"C:\$Recycle.Bin\S-1-5-21\z.exe", "high"),
    (r"C:\Users\Me\Downloads\setup.exe", "medium"),
    (r"C:\Users\Public\tool.exe", "medium"),
    (r"C:\Program Files\App\app.exe", None),
    (r"C:\Windows\System32\svchost.exe", None),
    ("", None),
])
def test_location_risk(exe, risk):
    assert location_risk(exe) == risk


def test_location_category():
    assert location_category(r"C:\Users\Me\AppData\Local\Temp\x.exe") == "temp"
    assert location_category(r"C:\Users\Me\Downloads\x.exe") == "downloads"
    assert location_category(r"C:\$Recycle.Bin\x.exe") == "recyclebin"


def app(**kwargs):
    base = dict(key="k", name="App", exe="", has_publisher=True,
                internet_ips=frozenset(), internet_ports=frozenset(), exposed_services={})
    base.update(kwargs)
    return AppActivity(**base)


def test_suspicious_location_needs_internet():
    quiet = app(key="a", exe=r"C:\Temp\a.exe")
    assert analyze([quiet]) == []
    active = app(key="b", exe=r"C:\Temp\b.exe", internet_ips=frozenset({"8.8.8.8"}), has_publisher=False)
    findings = analyze([active])
    assert len(findings) == 1
    assert findings[0].kind == "suspicious_location"
    assert findings[0].severity == "high"
    assert findings[0].data["unknown_publisher"] is True


def test_exposed_service_severity():
    smb = app(key="s", exposed_services={445: "SMB"})
    rdp_finding = analyze([smb])[0]
    assert rdp_finding.kind == "exposed_service" and rdp_finding.severity == "high"
    db = app(key="d", exposed_services={5432: "PostgreSQL"})
    assert analyze([db])[0].severity == "medium"


def test_host_fanout():
    ips = frozenset(f"1.2.3.{i}" for i in range(50))
    findings = analyze([app(internet_ips=ips)])
    assert any(f.kind == "host_fanout" and f.data["hosts"] == 50 for f in findings)


def test_unusual_port():
    findings = analyze([app(internet_ips=frozenset({"9.9.9.9"}), internet_ports=frozenset({443, 4444}))])
    port_finding = [f for f in findings if f.kind == "unusual_port"][0]
    assert port_finding.data["ports"] == [4444]
    assert port_finding.severity == "low"


def test_private_only_app_is_clean():
    assert analyze([app(internet_ips=frozenset(), internet_ports=frozenset({4444}))]) == []


def test_summary_and_ordering():
    activities = [
        app(key="low", name="Zebra", internet_ips=frozenset({"9.9.9.9"}), internet_ports=frozenset({1337})),
        app(key="high", name="Alpha", exe=r"C:\Temp\a.exe", internet_ips=frozenset({"8.8.8.8"})),
    ]
    findings = analyze(activities)
    assert findings[0].severity == "high"
    summary = summarize(findings)
    assert summary["counts"] == {"high": 1, "medium": 0, "low": 1}
    assert summary["level"] == "bad"


def test_summary_levels():
    assert summarize([])["level"] == "good"
    only_low = analyze([app(internet_ips=frozenset({"9.9.9.9"}), internet_ports=frozenset({31337}))])
    assert summarize(only_low)["level"] == "low"
