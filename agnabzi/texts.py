"""Strings used outside the web UI: tray menu, notifications and console output."""

from __future__ import annotations

from typing import Any

from agnabzi.events import Event

_UNITS_BYTES = ("B", "KB", "MB", "GB", "TB")

TEXTS = {
    "tr": {
        "open": "Paneli aç",
        "elevate": "Yönetici olarak yeniden başlat",
        "quit": "Çıkış",
        "rates": "↓ {down}   ↑ {up}",
        "running": "{name} {version} çalışıyor: {url}",
        "stop_hint": "Durdurmak için Ctrl+C tuşlarına basın.",
        "already_running": "{name} zaten çalışıyor, pano açılıyor.",
        "new_app_title": "Yeni uygulama internete bağlandı",
        "new_app": "{app} ilk kez internete bağlandı ({target}).",
        "quota_title": "Kota uyarısı",
        "quota": "Bu dönem kotanızın %{level} kadarını kullandınız ({used} / {quota}).",
        "offline_title": "İnternet bağlantısı kesildi",
        "offline": "Bağlantı yeniden kurulduğunda haber vereceğim.",
        "offline_local": "Modeme veya Wi-Fi ağına ulaşılamıyor.",
        "offline_internet": "Modeme ulaşılıyor ancak internet yok. Sorun büyük olasılıkla servis sağlayıcıda.",
        "online_title": "Bağlantı geri geldi",
        "online": "Kesinti süresi: {duration}.",
        "blocked_title": "İnternet erişimi engellendi",
        "blocked": "{app} artık internete bağlanamaz.",
    },
    "en": {
        "open": "Open dashboard",
        "elevate": "Restart as administrator",
        "quit": "Quit",
        "rates": "↓ {down}   ↑ {up}",
        "running": "{name} {version} is running: {url}",
        "stop_hint": "Press Ctrl+C to stop.",
        "already_running": "{name} is already running, opening the dashboard.",
        "new_app_title": "New app went online",
        "new_app": "{app} connected to the internet for the first time ({target}).",
        "quota_title": "Data cap warning",
        "quota": "You have used {level}% of this period's data cap ({used} / {quota}).",
        "offline_title": "Internet connection lost",
        "offline": "You will be notified when the connection is back.",
        "offline_local": "The router or Wi-Fi network cannot be reached.",
        "offline_internet": "The router responds but there is no internet. The provider is the likely cause.",
        "online_title": "Connection restored",
        "online": "Outage lasted {duration}.",
        "blocked_title": "Internet access blocked",
        "blocked": "{app} can no longer reach the internet.",
    },
}


def text(language: str, key: str, **values: Any) -> str:
    table = TEXTS.get(language, TEXTS["tr"])
    return table[key].format(**values)


def format_bytes(value: float) -> str:
    amount = float(value)
    for unit in _UNITS_BYTES:
        if abs(amount) < 1024 or unit == _UNITS_BYTES[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def format_rate(bytes_per_second: float, unit: str = "bits") -> str:
    if unit == "bytes":
        return f"{format_bytes(bytes_per_second)}/s"
    bits = bytes_per_second * 8
    for suffix, scale in (("Gbit/s", 1e9), ("Mbit/s", 1e6), ("kbit/s", 1e3)):
        if bits >= scale:
            return f"{bits / scale:.1f} {suffix}"
    return f"{bits:.0f} bit/s"


_DURATION_UNITS = {"tr": ("sn", "dk", "sa"), "en": ("s", "min", "h")}


def format_duration(seconds: float, language: str = "tr") -> str:
    sec, minute, hour = _DURATION_UNITS.get(language, _DURATION_UNITS["tr"])
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} {sec}"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} {minute} {seconds} {sec}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} {hour} {minutes} {minute}"


def notification(event: Event, language: str, settings: dict[str, Any]) -> tuple[str, str, bool] | None:
    """Return ``(title, message, is_warning)`` for events worth a desktop notification."""
    data = event.data
    if event.kind == "new_app" and settings["notify_new_apps"]:
        target = data.get("host") or data.get("ip", "")
        return text(language, "new_app_title"), text(language, "new_app", app=data["app"], target=target), True
    if event.kind == "quota" and settings["notify_quota"]:
        message = text(language, "quota", level=data["threshold"], used=format_bytes(data["used"]),
                       quota=format_bytes(data["quota"]))
        return text(language, "quota_title"), message, True
    if event.kind == "offline" and settings["notify_connectivity"]:
        key = data.get("code") if data.get("code") in ("offline_local", "offline_internet") else "offline"
        return text(language, "offline_title"), text(language, key), True
    if event.kind == "online" and settings["notify_connectivity"]:
        message = text(language, "online", duration=format_duration(data.get("seconds", 0), language))
        return text(language, "online_title"), message, False
    if event.kind == "blocked":
        return text(language, "blocked_title"), text(language, "blocked", app=data["app"]), False
    return None
