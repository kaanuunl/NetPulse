import json
from datetime import date

import pytest

from agnabzi.storage import SettingsError, Storage, billing_period


@pytest.mark.parametrize(
    "today, reset_day, expected",
    [
        (date(2026, 9, 23), 1, (date(2026, 9, 1), date(2026, 9, 30))),
        (date(2026, 9, 23), 15, (date(2026, 9, 15), date(2026, 10, 14))),
        (date(2026, 9, 10), 15, (date(2026, 8, 15), date(2026, 9, 14))),
        (date(2026, 2, 28), 31, (date(2026, 2, 28), date(2026, 3, 30))),
        (date(2026, 3, 5), 31, (date(2026, 2, 28), date(2026, 3, 30))),
        (date(2026, 1, 3), 10, (date(2025, 12, 10), date(2026, 1, 9))),
    ],
)
def test_billing_period(today, reset_day, expected):
    assert billing_period(today, reset_day) == expected


def test_usage_roundtrip(tmp_path):
    path = tmp_path / "usage.json"
    store = Storage(str(path))
    store.add_usage(1000, 200, date(2026, 9, 1))
    store.add_usage(500, 100, date(2026, 9, 1))
    store.add_app_usage("chrome.exe", 700, 50, date(2026, 9, 1))
    store.save(force=True)

    reloaded = Storage(str(path))
    daily = reloaded.daily(3, today=date(2026, 9, 2))
    assert daily == [
        {"date": "2026-08-31", "rx": 0, "tx": 0},
        {"date": "2026-09-01", "rx": 1500, "tx": 300},
        {"date": "2026-09-02", "rx": 0, "tx": 0},
    ]
    top = reloaded.top_apps(date(2026, 9, 1), date(2026, 9, 1))
    assert top == [{"app": "chrome.exe", "rx": 700, "tx": 50}]


def test_corrupt_file_is_moved_aside(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("{not json", encoding="utf-8")
    store = Storage(str(path))
    assert store.daily(1)[0]["rx"] == 0
    assert any(p.name.startswith("usage.json.corrupt-") for p in tmp_path.iterdir())


def test_wrong_types_fall_back_to_defaults(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"days": [], "settings": {"quota_gb": 5}}), encoding="utf-8")
    store = Storage(str(path))
    assert store.settings["quota_gb"] == 5
    store.add_usage(1, 1)


def test_summary_projection_and_quota():
    store = Storage.in_memory()
    store.update_settings({"quota_gb": 10, "quota_reset_day": 1})
    for day in range(1, 11):
        store.add_usage(900 * 1024**2, 100 * 1024**2, date(2026, 9, day))
    summary = store.usage_summary(today=date(2026, 9, 10))
    period = summary["period"]
    assert period["elapsed_days"] == 10
    assert period["total_days"] == 30
    assert period["rx"] + period["tx"] == 10 * 1000 * 1024**2
    assert period["projected"] == pytest.approx(30 * 1000 * 1024**2)
    assert summary["quota"]["used_ratio"] == pytest.approx(10 * 1000 / (10 * 1024))


def test_settings_validation():
    store = Storage.in_memory()
    assert store.update_settings({"language": "en", "quota_reset_day": 31})["language"] == "en"
    for bad in ({"language": "de"}, {"quota_reset_day": 0}, {"quota_gb": -1}, {"ping_target": "-c 5"},
                {"notify_quota": "yes"}, {"unknown": 1}):
        with pytest.raises(SettingsError):
            store.update_settings(bad)
    assert store.update_settings({"excluded_adapters": ["b", "a", "a"]})["excluded_adapters"] == ["a", "b"]
    assert store.update_settings({"excluded_adapters": None})["excluded_adapters"] is None


def test_quota_alerts_fire_once_per_period():
    store = Storage.in_memory()
    assert store.quota_alert_due("2026-09-01", 80)
    assert not store.quota_alert_due("2026-09-01", 80)
    assert store.quota_alert_due("2026-09-01", 100)
    assert store.quota_alert_due("2026-10-01", 80)


def test_app_breakdown_folds_into_other_after_limit():
    store = Storage.in_memory()
    for index in range(70):
        store.add_app_usage(f"app{index}.exe", 10, 1, date(2026, 9, 1))
    apps = {entry["app"]: entry for entry in store.top_apps(date(2026, 9, 1), date(2026, 9, 1), limit=100)}
    assert len(apps) == 61
    assert apps["__other__"]["rx"] == 100


def test_monthly_and_csv():
    store = Storage.in_memory()
    store.add_usage(10, 1, date(2026, 8, 31))
    store.add_usage(20, 2, date(2026, 9, 1))
    store.add_app_usage("x.exe", 5, 1, date(2026, 9, 1))
    months = store.monthly(2, today=date(2026, 9, 15))
    assert months == [{"month": "2026-08", "rx": 10, "tx": 1}, {"month": "2026-09", "rx": 20, "tx": 2}]
    csv_text = store.export_csv()
    assert "2026-09-01,20,2,,," in csv_text
    assert "2026-09-01,,,x.exe,5,1" in csv_text


def test_prune_drops_old_days():
    store = Storage.in_memory()
    store.add_usage(1, 1, date(2020, 1, 1))
    store.add_usage(1, 1, date(2026, 9, 1))
    store.prune(today=date(2026, 9, 2))
    assert store.monthly(1, today=date(2020, 1, 15))[0]["rx"] == 0
