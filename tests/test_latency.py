import pytest

from agnabzi.latency import LatencyTarget, compute_stats, diagnose


def stats(values, replies_total=None):
    return compute_stats(values, sum(v is not None for v in values) if replies_total is None else replies_total)


def test_compute_stats():
    result = stats([10.0, 12.0, None, 14.0])
    assert result.avg == pytest.approx(12.0)
    assert result.minimum == 10.0 and result.maximum == 14.0
    assert result.jitter == pytest.approx(2.0)
    assert result.loss == pytest.approx(0.25)
    assert result.recent_loss == pytest.approx(1 / 3)


def test_empty_stats():
    result = stats([])
    assert result.avg is None and result.loss == 0.0 and result.samples == 0


HEALTHY_GATEWAY = [2.0] * 20
HEALTHY_INTERNET = [20.0, 21.0] * 10


def test_diagnosis_good_and_unknown():
    assert diagnose(stats(HEALTHY_GATEWAY), stats(HEALTHY_INTERNET)) == "good"
    assert diagnose(stats(HEALTHY_GATEWAY), stats([20.0, 21.0])) == "unknown"


def test_single_loss_is_tolerated():
    assert diagnose(stats(HEALTHY_GATEWAY), stats(HEALTHY_INTERNET[:-1] + [None] + [20.0] * 10)) == "good"


def test_isp_side_loss():
    internet = [20.0, None, 21.0, None, 20.0, None] + [20.0] * 14
    assert diagnose(stats(HEALTHY_GATEWAY), stats(internet)) == "unstable_internet"


def test_local_network_problem():
    gateway = [2.0, 90.0, 3.0, 120.0] * 5
    assert diagnose(stats(gateway), stats(HEALTHY_INTERNET)) == "unstable_local"


def test_offline_classification():
    offline = [20.0] * 10 + [None, None, None]
    assert diagnose(stats(HEALTHY_GATEWAY + [2.0] * 3), stats(offline)) == "offline_internet"
    assert diagnose(stats(HEALTHY_GATEWAY + [None] * 3), stats(offline)) == "offline_local"
    assert diagnose(None, stats(offline)) == "offline"


def test_router_that_ignores_ping_is_not_blamed():
    silent_router = stats([None] * 20, replies_total=0)
    assert diagnose(silent_router, stats(HEALTHY_INTERNET)) == "good"


def test_high_latency():
    assert diagnose(stats(HEALTHY_GATEWAY), stats([180.0, 185.0] * 10)) == "slow_internet"


def test_target_resets_when_host_changes():
    hosts = iter(["192.168.1.1", "10.0.0.1"])
    target = LatencyTarget("gateway", lambda: next(hosts), lambda: None, interval=1)
    target._current_ip()
    target.record(3.0)
    assert target.stats().samples == 1
    target._host_checked = 0
    target._ip = None
    target._current_ip()
    assert target.stats().samples == 0
    assert target.host == "10.0.0.1"
