import http.client
import json
import re
import threading

import pytest

from agnabzi import demo
from agnabzi.api import Api
from agnabzi.collector import Collector
from agnabzi.events import EventLog
from agnabzi.latency import LatencyMonitor
from agnabzi.netinfo import ReverseDns
from agnabzi.platforms import DemoPlatform
from agnabzi.server import DashboardServer
from agnabzi.storage import Storage

TOKEN = "test-token-123"


@pytest.fixture(scope="module")
def server():
    network = demo.DemoNetwork()
    storage = Storage.in_memory()
    demo.seed_history(storage, days=10)
    events = EventLog()
    rdns = ReverseDns(static=demo.DEMO_HOSTNAMES)
    latency = LatencyMonitor("1.1.1.1", demo.DemoPinger, lambda: demo.DEMO_GATEWAY)
    collector = Collector(demo.DemoProbe(network), storage, events, rdns, demo.DemoTrafficSource(network), latency)
    collector.tick()
    collector.tick()
    instance = DashboardServer(0, TOKEN)
    instance.api = Api(
        collector=collector, storage=storage, events=events, latency=latency, rdns=rdns,
        platform=DemoPlatform(), pinger_factory=demo.DemoPinger, demo=True, port=instance.port,
        request_quit=lambda: None, speedtest_factory=demo.DemoSpeedTest, trace=demo.demo_trace,
    )
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    yield instance
    instance.shutdown()
    instance.server_close()
    rdns.shutdown()


def call(server, method, path, body=None, token=TOKEN, host=None, content_type="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    headers = {}
    if token:
        headers["X-NetPulse-Token"] = token
    if host:
        headers["Host"] = host
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = content_type
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_index_embeds_token_and_security_headers(server):
    response, body = call(server, "GET", "/", token=None)
    assert response.status == 200
    assert re.search(rb'name="netpulse-token" content="test-token-123"', body)
    csp = response.getheader("Content-Security-Policy")
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert response.getheader("X-Frame-Options") == "DENY"


def test_api_requires_token(server):
    response, body = call(server, "GET", "/api/overview", token=None)
    assert response.status == 403 and json.loads(body)["error"] == "token"
    response, _ = call(server, "GET", "/api/overview", token="wrong")
    assert response.status == 403


def test_foreign_host_header_is_rejected(server):
    response, body = call(server, "GET", "/", token=None, host="attacker.example:8765")
    assert response.status == 403 and json.loads(body)["error"] == "host"
    response, _ = call(server, "GET", "/api/overview", host="attacker.example")
    assert response.status == 403


def test_ping_is_public(server):
    response, body = call(server, "GET", "/api/ping", token=None)
    assert response.status == 200 and json.loads(body)["app"] == "NetPulse"


def test_static_files_and_traversal(server):
    response, body = call(server, "GET", "/static/js/main.js", token=None)
    assert response.status == 200 and response.getheader("Content-Type").startswith("text/javascript")
    for path in ("/static/../server.py", "/static/..%2f..%2fserver.py", "/static/index.html", "/etc/passwd"):
        response, _ = call(server, "GET", path, token=None)
        assert response.status == 404, path


@pytest.mark.parametrize("path", ["/api/overview", "/api/apps", "/api/connections", "/api/adapters",
                                  "/api/history?days=30", "/api/settings"])
def test_read_endpoints(server, path):
    response, body = call(server, "GET", path)
    assert response.status == 200
    assert isinstance(json.loads(body), dict)


def test_settings_update_and_validation(server):
    response, body = call(server, "POST", "/api/settings", {"settings": {"quota_gb": 250}})
    assert response.status == 200 and json.loads(body)["settings"]["quota_gb"] == 250
    response, body = call(server, "POST", "/api/settings", {"settings": {"quota_reset_day": 99}})
    assert response.status == 400
    assert json.loads(body) == {"error": "invalid_setting", "field": "quota_reset_day"}


def test_post_requires_json(server):
    response, body = call(server, "POST", "/api/tools/dns", {"name": "x"}, content_type="text/plain")
    assert response.status == 415


def test_tool_input_is_validated(server):
    response, body = call(server, "POST", "/api/tools/ping", {"host": "--help"})
    assert response.status == 400 and json.loads(body)["error"] == "invalid_host"
    response, body = call(server, "POST", "/api/tools/port", {"host": "localhost", "port": 70000})
    assert json.loads(body)["error"] == "invalid_port"


def test_block_and_unblock_roundtrip(server):
    apps = json.loads(call(server, "GET", "/api/apps")[1])["apps"]
    chrome = next(a for a in apps if a["name"] == "chrome.exe")
    assert call(server, "POST", "/api/apps/block", {"key": chrome["key"]})[0].status == 200
    apps = json.loads(call(server, "GET", "/api/apps")[1])["apps"]
    assert next(a for a in apps if a["name"] == "chrome.exe")["blocked"]
    assert call(server, "POST", "/api/apps/unblock", {"key": chrome["key"]})[0].status == 200
    response, body = call(server, "POST", "/api/apps/block", {"key": "c:\\nope.exe"})
    assert response.status == 404


def test_jobs(server):
    response, body = call(server, "POST", "/api/tools/traceroute", {"host": "example.com"})
    job = json.loads(body)
    assert job["kind"] == "traceroute" and job["state"] == "running"
    response, body = call(server, "GET", f"/api/jobs/{job['id']}")
    assert response.status == 200
    response, body = call(server, "POST", f"/api/jobs/{job['id']}/cancel", {})
    assert response.status == 200
    assert call(server, "GET", "/api/jobs/missing-1")[0].status == 404


def test_csv_export(server):
    response, body = call(server, "GET", "/api/export.csv")
    assert response.status == 200
    assert body.decode("utf-8-sig").startswith("date,download_bytes")


def test_security_endpoint(server):
    response, body = call(server, "GET", "/api/security")
    assert response.status == 200
    data = json.loads(body)
    assert "findings" in data["threats"] and "summary" in data["threats"]
    assert data["posture"]["dpi"]["active"] is True
    # The demo seeds an SMB listener on all interfaces, which is a high finding.
    assert any(f["kind"] == "exposed_service" for f in data["threats"]["findings"])


def test_dns_check_validates_host(server):
    response, body = call(server, "POST", "/api/tools/dnscheck", {"name": "-bad-"})
    assert response.status == 400 and json.loads(body)["error"] == "invalid_host"


def test_tool_rate_limit(server):
    server.tool_limiter._hits.clear()
    seen = set()
    for _ in range(server.tool_limiter._limit + 3):
        seen.add(call(server, "POST", "/api/tools/dns", {"name": "localhost"})[0].status)
    assert 429 in seen
    server.tool_limiter._hits.clear()
