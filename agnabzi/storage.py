"""Persistent usage history, settings and small bits of application state.

Everything lives in a single JSON document that is rewritten atomically. The
document is small (one entry per day plus a per-application breakdown), so a
database would add weight without buying anything.
"""

from __future__ import annotations

import calendar
import contextlib
import copy
import csv
import io
import json
import logging
import os
import threading
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any, Callable

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
HISTORY_DAYS = 800
APPS_PER_DAY = 60
SPEEDTEST_HISTORY = 100

DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "en",
    "theme": "auto",
    "speed_unit": "bits",
    "quota_gb": 0.0,
    "quota_reset_day": 1,
    "ping_target": "1.1.1.1",
    "notify_new_apps": True,
    "notify_quota": True,
    "notify_connectivity": True,
    "excluded_adapters": None,
}

_SETTING_RULES: dict[str, Callable[[Any], Any]] = {}


def _rule(name: str):
    def register(fn):
        _SETTING_RULES[name] = fn
        return fn

    return register


@_rule("language")
def _language(value):
    return value if value in ("tr", "en") else None


@_rule("theme")
def _theme(value):
    return value if value in ("auto", "dark", "light") else None


@_rule("speed_unit")
def _speed_unit(value):
    return value if value in ("bits", "bytes") else None


@_rule("quota_gb")
def _quota(value):
    number = float(value)
    return round(number, 2) if 0 <= number <= 1_000_000 else None


@_rule("quota_reset_day")
def _reset_day(value):
    number = int(value)
    return number if 1 <= number <= 31 else None


@_rule("ping_target")
def _ping_target(value):
    from agnabzi.netinfo import is_valid_host

    value = str(value).strip()
    return value if is_valid_host(value) else None


@_rule("excluded_adapters")
def _excluded(value):
    if value is None:
        return None
    return sorted({str(v)[:256] for v in value})[:64]


def _strict_bool(value):
    return value if isinstance(value, bool) else None


for _flag in ("notify_new_apps", "notify_quota", "notify_connectivity"):
    _SETTING_RULES[_flag] = _strict_bool


class SettingsError(ValueError):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def day_key(moment: float | date | None = None) -> str:
    if moment is None:
        return date.today().isoformat()
    if isinstance(moment, date):
        return moment.isoformat()
    return datetime.fromtimestamp(moment).date().isoformat()


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def billing_period(today: date, reset_day: int) -> tuple[date, date]:
    """Return the first and last day of the billing period containing ``today``."""
    start = _clamped(today.year, today.month, reset_day)
    if today < start:
        year, month = _shift_month(today.year, today.month, -1)
        start = _clamped(year, month, reset_day)
    year, month = _shift_month(start.year, start.month, 1)
    next_start = _clamped(year, month, reset_day)
    return start, next_start - timedelta(days=1)


def _empty_document() -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "days": {},
        "settings": {},
        "known_apps": {},
        "app_meta": {},
        "blocked_apps": {},
        "speedtests": [],
        "quota_alerts": {},
    }


class Storage:
    def __init__(self, path: str | None, autosave_interval: float = 15.0):
        self.path = path
        self._lock = threading.RLock()
        self._dirty = False
        self._last_save = 0.0
        self._autosave_interval = autosave_interval
        self._doc = self._load()

    @classmethod
    def in_memory(cls) -> Storage:
        return cls(None)

    # persistence -----------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        doc = _empty_document()
        if not self.path or not os.path.exists(self.path):
            return doc
        try:
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                raise ValueError("root is not an object")
        except (OSError, ValueError) as exc:
            backup = f"{self.path}.corrupt-{int(time.time())}"
            log.warning("usage file unreadable (%s), moving it to %s", exc, backup)
            with contextlib.suppress(OSError):
                os.replace(self.path, backup)
            return doc
        for key, default in doc.items():
            value = loaded.get(key, default)
            doc[key] = value if isinstance(value, type(default)) else default
        return doc

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not self.path or not (self._dirty or force):
                return
            payload = json.dumps(self._doc, ensure_ascii=False, separators=(",", ":"))
            self._dirty = False
            self._last_save = time.monotonic()
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("could not save usage data: %s", exc)
            with self._lock:
                self._dirty = True

    def maybe_save(self) -> None:
        if time.monotonic() - self._last_save >= self._autosave_interval:
            self.save()

    def _touch(self) -> None:
        self._dirty = True

    # settings --------------------------------------------------------------------

    @property
    def settings(self) -> dict[str, Any]:
        with self._lock:
            merged = copy.deepcopy(DEFAULT_SETTINGS)
            merged.update(self._doc["settings"])
            return merged

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        cleaned = {}
        for name, value in changes.items():
            rule = _SETTING_RULES.get(name)
            if rule is None:
                raise SettingsError(name)
            try:
                result = rule(value)
            except (TypeError, ValueError):
                result = None
            if result is None and not (name == "excluded_adapters" and value is None):
                raise SettingsError(name)
            cleaned[name] = result
        with self._lock:
            self._doc["settings"].update(cleaned)
            self._touch()
        return self.settings

    # usage -----------------------------------------------------------------------

    def _day(self, key: str) -> dict[str, Any]:
        day = self._doc["days"].get(key)
        if day is None:
            day = self._doc["days"][key] = {"rx": 0, "tx": 0, "apps": {}}
        return day

    def add_usage(self, rx: int, tx: int, moment: float | date | None = None) -> None:
        if rx <= 0 and tx <= 0:
            return
        with self._lock:
            day = self._day(day_key(moment))
            day["rx"] += int(rx)
            day["tx"] += int(tx)
            self._touch()

    def add_app_usage(self, app: str, rx: int, tx: int, moment: float | date | None = None) -> None:
        if rx <= 0 and tx <= 0:
            return
        with self._lock:
            apps = self._day(day_key(moment))["apps"]
            entry = apps.get(app)
            if entry is None:
                if len(apps) >= APPS_PER_DAY:
                    app = "__other__"
                    entry = apps.setdefault(app, [0, 0])
                else:
                    entry = apps[app] = [0, 0]
            entry[0] += int(rx)
            entry[1] += int(tx)
            self._touch()

    def _days_between(self, start: date, end: date) -> Iterable[tuple[date, dict[str, Any]]]:
        current = start
        while current <= end:
            yield current, self._doc["days"].get(current.isoformat())
            current += timedelta(days=1)

    def daily(self, count: int, today: date | None = None) -> list[dict[str, Any]]:
        today = today or date.today()
        start = today - timedelta(days=count - 1)
        with self._lock:
            return [
                {"date": d.isoformat(), "rx": e["rx"] if e else 0, "tx": e["tx"] if e else 0}
                for d, e in self._days_between(start, today)
            ]

    def monthly(self, count: int = 12, today: date | None = None) -> list[dict[str, Any]]:
        today = today or date.today()
        totals: dict[str, list[int]] = {}
        with self._lock:
            for key, entry in self._doc["days"].items():
                bucket = totals.setdefault(key[:7], [0, 0])
                bucket[0] += entry["rx"]
                bucket[1] += entry["tx"]
        months = []
        for offset in range(count - 1, -1, -1):
            year, month = _shift_month(today.year, today.month, -offset)
            label = f"{year:04d}-{month:02d}"
            rx, tx = totals.get(label, (0, 0))
            months.append({"month": label, "rx": rx, "tx": tx})
        return months

    def top_apps(self, start: date, end: date, limit: int = 15) -> list[dict[str, Any]]:
        totals: dict[str, list[int]] = {}
        with self._lock:
            for _, entry in self._days_between(start, end):
                if not entry:
                    continue
                for app, (rx, tx) in entry["apps"].items():
                    bucket = totals.setdefault(app, [0, 0])
                    bucket[0] += rx
                    bucket[1] += tx
            meta = dict(self._doc["app_meta"])
        ranked = sorted(totals.items(), key=lambda item: item[1][0] + item[1][1], reverse=True)
        return [
            {"app": app, "rx": rx, "tx": tx, **meta.get(app, {})}
            for app, (rx, tx) in ranked[:limit]
        ]

    def usage_summary(self, today: date | None = None) -> dict[str, Any]:
        today = today or date.today()
        settings = self.settings
        start, end = billing_period(today, settings["quota_reset_day"])
        with self._lock:
            today_entry = self._doc["days"].get(today.isoformat()) or {"rx": 0, "tx": 0}
            period_rx = period_tx = 0
            for _, entry in self._days_between(start, today):
                if entry:
                    period_rx += entry["rx"]
                    period_tx += entry["tx"]
        elapsed_days = (today - start).days + 1
        total_days = (end - start).days + 1
        used = period_rx + period_tx
        daily_average = used / elapsed_days
        quota_bytes = int(settings["quota_gb"] * 1024**3)
        return {
            "today": {"rx": today_entry["rx"], "tx": today_entry["tx"]},
            "period": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rx": period_rx,
                "tx": period_tx,
                "elapsed_days": elapsed_days,
                "total_days": total_days,
                "daily_average": daily_average,
                "projected": int(daily_average * total_days),
            },
            "quota": {
                "bytes": quota_bytes,
                "used_ratio": used / quota_bytes if quota_bytes else None,
                "remaining": max(quota_bytes - used, 0) if quota_bytes else None,
            },
        }

    def export_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["date", "download_bytes", "upload_bytes", "app", "app_download_bytes", "app_upload_bytes"])
        with self._lock:
            for key in sorted(self._doc["days"]):
                entry = self._doc["days"][key]
                writer.writerow([key, entry["rx"], entry["tx"], "", "", ""])
                for app, (rx, tx) in sorted(entry["apps"].items(), key=lambda i: -(i[1][0] + i[1][1])):
                    writer.writerow([key, "", "", app, rx, tx])
        return buffer.getvalue()

    def reset_history(self) -> None:
        with self._lock:
            self._doc["days"] = {}
            self._doc["quota_alerts"] = {}
            self._touch()

    def prune(self, today: date | None = None) -> None:
        cutoff = ((today or date.today()) - timedelta(days=HISTORY_DAYS)).isoformat()
        with self._lock:
            stale = [key for key in self._doc["days"] if key < cutoff]
            for key in stale:
                del self._doc["days"][key]
            if stale:
                self._touch()

    # quota alert bookkeeping -----------------------------------------------------

    def quota_alert_due(self, period_start: str, level: int) -> bool:
        """Record ``level`` as announced for the period; True the first time only."""
        with self._lock:
            announced = self._doc["quota_alerts"].get(period_start, [])
            if level in announced:
                return False
            self._doc["quota_alerts"] = {period_start: sorted({*announced, level})}
            self._touch()
            return True

    # known / blocked applications ------------------------------------------------

    def is_known_app(self, key: str) -> bool:
        with self._lock:
            return key in self._doc["known_apps"]

    def has_known_apps(self) -> bool:
        with self._lock:
            return bool(self._doc["known_apps"])

    def remember_app(self, key: str, when: float | None = None) -> None:
        with self._lock:
            if key not in self._doc["known_apps"]:
                self._doc["known_apps"][key] = int(when or time.time())
                self._touch()

    def remember_app_meta(self, name: str, title: str, exe: str) -> None:
        entry = {"title": title, "exe": exe}
        with self._lock:
            if self._doc["app_meta"].get(name) != entry:
                self._doc["app_meta"][name] = entry
                self._touch()

    def blocked_apps(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._doc["blocked_apps"])

    def set_blocked(self, exe: str, info: dict[str, Any] | None) -> None:
        with self._lock:
            if info is None:
                self._doc["blocked_apps"].pop(exe, None)
            else:
                self._doc["blocked_apps"][exe] = info
            self._touch()

    # speed tests -----------------------------------------------------------------

    def add_speedtest(self, result: dict[str, Any]) -> None:
        with self._lock:
            tests = self._doc["speedtests"]
            tests.append(result)
            del tests[:-SPEEDTEST_HISTORY]
            self._touch()

    def speedtests(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._doc["speedtests"])
