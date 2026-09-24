import socket
import struct

from agnabzi.traffic import FlowAccumulator, pack_ip, parse_kernel_network, unpack_ip


def ipv4_payload(pid, size, daddr, saddr, dport=443, sport=51000):
    return (
        struct.pack("<II", pid, size)
        + socket.inet_aton(daddr)
        + socket.inet_aton(saddr)
        + struct.pack(">HH", dport, sport)
        + b"\x00" * 16
    )


def test_parse_ipv4_send_and_receive():
    sent = parse_kernel_network(10, ipv4_payload(1234, 1500, "93.184.216.34", "192.168.1.5"))
    assert sent.pid == 1234 and sent.size == 1500 and sent.sent
    assert unpack_ip(sent.daddr) == "93.184.216.34"
    received = parse_kernel_network(43, ipv4_payload(77, 64, "1.1.1.1", "10.0.0.2"))
    assert received.pid == 77 and not received.sent


def test_parse_ipv6():
    daddr = socket.inet_pton(socket.AF_INET6, "2606:4700::1111")
    saddr = socket.inet_pton(socket.AF_INET6, "fd00::2")
    payload = struct.pack("<II", 9, 300) + daddr + saddr + b"\x01\xbb\xc3\x50"
    packet = parse_kernel_network(27, payload)
    assert packet.pid == 9 and not packet.sent
    assert unpack_ip(packet.daddr) == "2606:4700::1111"


def test_parse_rejects_unknown_or_short_events():
    assert parse_kernel_network(12, ipv4_payload(1, 1, "1.1.1.1", "2.2.2.2")) is None
    assert parse_kernel_network(10, b"\x00" * 10) is None


def test_accumulator_picks_the_remote_side_for_either_convention():
    local = {pack_ip("192.168.1.5")}
    flows = FlowAccumulator()
    remote = socket.inet_aton("93.184.216.34")
    me = socket.inet_aton("192.168.1.5")
    flows.add(100, remote, me, 1000, sent=False)
    flows.add(100, me, remote, 500, sent=False)
    flows.add(100, remote, me, 200, sent=True)
    samples = flows.drain(local)
    assert len(samples) == 1
    assert samples[0].remote_ip == "93.184.216.34"
    assert (samples[0].rx, samples[0].tx) == (1500, 200)
    assert flows.drain(local) == []


def test_ipv4_mapped_addresses_are_shown_as_ipv4():
    mapped = b"\x00" * 10 + b"\xff\xff" + socket.inet_aton("8.8.8.8")
    assert unpack_ip(mapped) == "8.8.8.8"
    assert pack_ip("fe80::1%12") == socket.inet_pton(socket.AF_INET6, "fe80::1")
    assert pack_ip("not an ip") is None
