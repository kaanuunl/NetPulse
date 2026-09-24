import pytest

from agnabzi.netinfo import (
    classify_ip,
    dns_verdict,
    is_valid_host,
    parse_proc_net_route,
    service_name,
)
from agnabzi.traceroute import parse_traceroute_line


@pytest.mark.parametrize("system, doh, doh_ok, verdict", [
    (["93.184.216.34"], ["93.184.216.34"], True, "ok"),
    (["104.16.0.1"], ["93.184.216.34"], True, "ok"),           # different but both public → CDN, not tampering
    (["127.0.0.1"], ["93.184.216.34"], True, "tampered"),      # local sinkhole / block page
    (["10.0.0.1"], ["93.184.216.34"], True, "tampered"),
    (["0.0.0.0"], ["93.184.216.34"], True, "tampered"),
    ([], ["93.184.216.34"], True, "blocked"),                  # NXDOMAIN locally, resolvable over DoH
    (["93.184.216.34"], [], False, "doh_failed"),
    (["127.0.0.1", "93.184.216.34"], ["93.184.216.34"], True, "ok"),  # a public answer present → not a block
])
def test_dns_verdict(system, doh, doh_ok, verdict):
    assert dns_verdict(system, doh, doh_ok) == verdict


@pytest.mark.parametrize(
    "ip, scope",
    [
        ("8.8.8.8", "public"),
        ("192.168.1.10", "private"),
        ("10.1.2.3", "private"),
        ("100.72.1.1", "private"),
        ("127.0.0.1", "loopback"),
        ("::1", "loopback"),
        ("0.0.0.0", "unspecified"),
        ("::", "unspecified"),
        ("224.0.0.251", "multicast"),
        ("169.254.10.1", "link-local"),
        ("fe80::1%3", "link-local"),
        ("::ffff:1.1.1.1", "public"),
        ("2606:4700::1111", "public"),
        ("garbage", "unknown"),
    ],
)
def test_classify_ip(ip, scope):
    assert classify_ip(ip) == scope


@pytest.mark.parametrize("host", ["google.com", "1.1.1.1", "my-router.local", "2001:db8::1", "a"])
def test_valid_hosts(host):
    assert is_valid_host(host)


@pytest.mark.parametrize("host", ["", "-c", "a b", "x;rm", "host/../x", "a" * 300, "http://x"])
def test_invalid_hosts(host):
    assert not is_valid_host(host)


def test_parse_proc_net_route():
    table = (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t0000A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n"
    )
    assert parse_proc_net_route(table) == "192.168.1.1"
    assert parse_proc_net_route(table.splitlines()[0]) is None


def test_service_names():
    assert service_name(443) == "HTTPS"
    assert service_name(65000) == ""


def test_parse_traceroute_lines():
    assert parse_traceroute_line(" 1  192.168.1.1  0.412 ms  0.388 ms  0.371 ms") == {
        "ttl": 1, "ip": "192.168.1.1", "rtts": [0.412, 0.388, 0.371],
    }
    assert parse_traceroute_line(" 7  * * *") == {"ttl": 7, "ip": None, "rtts": [None, None, None]}
    assert parse_traceroute_line("traceroute to x (1.2.3.4), 30 hops max") is None
