import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agnabzi.speedtest import SpeedTest, parse_meta


class FakeSpeedServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/meta":
            body = json.dumps({
                "colo": {"iata": "TST", "city": "Testville", "cca2": "TR"},
                "asOrganization": "Loopback ISP",
                "clientIp": "127.0.0.1",
            }).encode()
            self._reply(body, "application/json")
            return
        size = int(self.path.split("bytes=")[1])
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Server-Timing", "cfRequestDuration;dur=0.5")
        self.end_headers()
        chunk = b"\x00" * 65536
        remaining = size
        try:
            while remaining > 0:
                piece = chunk[: min(remaining, len(chunk))]
                self.wfile.write(piece)
                remaining -= len(piece)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        remaining = int(self.headers["Content-Length"])
        try:
            while remaining > 0:
                data = self.rfile.read(min(remaining, 65536))
                if not data:
                    return
                remaining -= len(data)
        except ConnectionError:
            return
        self._reply(b"ok", "text/plain")

    def _reply(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def speed_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSpeedServer)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_speedtest_against_local_server(speed_server):
    phases = []
    test = SpeedTest(server=speed_server, duration=2.0, streams=2, on_progress=lambda s: phases.append(s["phase"]))
    result = test.run()
    assert result["ok"]
    assert result["ping_ms"] is not None
    assert result["download_bps"] > 1e6
    assert result["upload_bps"] > 1e6
    assert result["server"] == {
        "colo": "TST", "city": "Testville", "country": "TR", "isp": "Loopback ISP", "ip": "127.0.0.1",
    }
    assert {"latency", "download", "upload", "done"} <= set(phases)


def test_unreachable_server_reports_error():
    result = SpeedTest(server="http://127.0.0.1:9", duration=0.5, timeout=1).run()
    assert result == {"ok": False, "error": "unreachable"}


def test_parse_meta_accepts_string_colo():
    assert parse_meta({"colo": "IST", "city": "Istanbul"})["colo"] == "IST"
    assert parse_meta({})["colo"] == ""
