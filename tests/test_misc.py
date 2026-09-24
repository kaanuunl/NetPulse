import struct
import time
import zlib

import pytest

from agnabzi.events import Event, EventLog
from agnabzi.jobs import JobManager
from agnabzi.png import bgra_to_rgba, encode_rgba
from agnabzi.storage import DEFAULT_SETTINGS
from agnabzi.texts import format_bytes, format_duration, format_rate, notification
from agnabzi.windows.firewall import FirewallError, add_rule_command, ensure_blockable, rule_name


def test_png_encoder_produces_valid_image():
    rgba = bytes([255, 0, 0, 255]) * 4
    png = encode_rgba(2, 2, rgba)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (2, 2)
    idat_length = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41 : 41 + idat_length])
    assert raw == (b"\x00" + rgba[:8]) * 2
    with pytest.raises(ValueError):
        encode_rgba(3, 3, rgba)


def test_bgra_conversion():
    assert bgra_to_rgba(bytes([1, 2, 3, 4])) == bytes([3, 2, 1, 4])


def test_formatting():
    assert format_bytes(512) == "512 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_rate(125_000) == "1.0 Mbit/s"
    assert format_rate(2 * 1024 * 1024, "bytes") == "2.0 MB/s"
    assert format_duration(75, "tr") == "1 dk 15 sn"
    assert format_duration(3700, "en") == "1 h 1 min"


def test_notifications_respect_settings():
    event = Event(1, time.time(), "new_app", "warn", {"app": "Game", "ip": "1.2.3.4", "host": ""})
    title, message, warning = notification(event, "tr", DEFAULT_SETTINGS)
    assert "Game" in message and warning
    assert notification(event, "en", {**DEFAULT_SETTINGS, "notify_new_apps": False}) is None
    quiet = Event(2, time.time(), "baseline", "info", {"count": 3})
    assert notification(quiet, "tr", DEFAULT_SETTINGS) is None
    quota = Event(3, time.time(), "quota", "warn", {"threshold": 80, "used": 8 * 1024**3, "quota": 10 * 1024**3})
    assert "%80" in notification(quota, "tr", DEFAULT_SETTINGS)[1]


def test_event_log_listeners_and_since():
    log = EventLog(capacity=3)
    seen = []
    log.subscribe(seen.append)
    log.subscribe(lambda e: 1 / 0)
    for index in range(5):
        log.add("x", n=index)
    assert [e["data"]["n"] for e in log.since(0)] == [2, 3, 4]
    assert [e["data"]["n"] for e in log.since(4)] == [4]
    assert len(seen) == 5


def test_firewall_rule_building():
    exe = r"C:\Program Files\Game\game.exe"
    name = rule_name(exe)
    assert name.startswith("NetPulse block game.exe ")
    assert name.split()[-1] == rule_name(exe.upper()).split()[-1]
    command = add_rule_command(name, exe, "out")
    assert f'program="{exe}"' in command and "action=block" in command and "dir=out" in command


@pytest.mark.parametrize("exe, code", [
    (r"C:\Windows\System32\svchost.exe", "protected"),
    (r"C:\Windows\explorer.exe", "protected"),
    (r"C:\WINDOWS\System32\anything.exe", "protected"),
    (r"C:\NetPulse\NetPulse.exe", "protected"),
    ("relative.exe", "invalid_path"),
    (r'C:\a"b\x.exe', "invalid_path"),
    ("", "invalid_path"),
])
def test_firewall_refuses_dangerous_targets(exe, code):
    with pytest.raises(FirewallError) as info:
        ensure_blockable(exe, r"C:\NetPulse\NetPulse.exe")
    assert info.value.code == code


def test_firewall_allows_ordinary_program():
    ensure_blockable(r"C:\Program Files\Game\game.exe", r"C:\NetPulse\NetPulse.exe")


def test_job_manager_exclusive_and_errors():
    manager = JobManager()
    gate = __import__("threading").Event()
    first = manager.start("speed", lambda job: gate.wait(2), exclusive=True)
    second = manager.start("speed", lambda job: None, exclusive=True)
    assert first is second
    gate.set()

    class Boom(Exception):
        code = "boom"

    def fail(job):
        raise Boom()

    failing = manager.start("trace", fail)
    for _ in range(50):
        if failing.state != "running":
            break
        time.sleep(0.02)
    assert failing.state == "error" and failing.error == "boom"
