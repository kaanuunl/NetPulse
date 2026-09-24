"""Operating-system specific capabilities behind one interface."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

from agnabzi.system import IS_WINDOWS, is_admin, launch_command, project_root

log = logging.getLogger(__name__)


class PlatformError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class BasePlatform:
    name = "generic"
    supports_firewall = False
    supports_autostart = False
    supports_elevation = False
    supports_icons = False

    def __init__(self) -> None:
        self.admin = is_admin()

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": self.name,
            "admin": self.admin,
            "firewall": self.supports_firewall,
            "autostart": self.supports_autostart,
            "elevation": self.supports_elevation and not self.admin,
            "icons": self.supports_icons,
        }

    def block(self, exe: str) -> None:
        raise PlatformError("unsupported")

    def unblock(self, exe: str) -> None:
        raise PlatformError("unsupported")

    def reveal(self, exe: str) -> None:
        if not exe or not os.path.exists(exe):
            raise PlatformError("not_found")
        folder = os.path.dirname(exe)
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise PlatformError("unsupported") from exc

    def icon(self, exe: str) -> bytes | None:
        return None

    def autostart_status(self) -> dict[str, Any]:
        return {"enabled": False, "mode": None}

    def set_autostart(self, enabled: bool) -> dict[str, Any]:
        raise PlatformError("unsupported")

    def elevate(self, args: list[str]) -> bool:
        raise PlatformError("unsupported")

    def dpi_status(self) -> dict[str, Any]:
        """Detect an external DPI-circumvention tool the user installed themselves."""
        return {"supported": False, "active": False, "tools": []}

    def shutdown(self) -> None:
        pass


# Known anti-censorship / DPI-bypass tools, detected by process name. NetPulse
# never bundles or drives one; it only reports whether the user is running it.
DPI_TOOLS = {
    "goodbyedpi.exe": "GoodbyeDPI",
    "winws.exe": "zapret",
    "spoofdpi.exe": "SpoofDPI",
    "greentunnel.exe": "GreenTunnel",
    "dpitunnel.exe": "DPITunnel",
    "zapret.exe": "zapret",
}


def _running_dpi_tools() -> list[str]:
    import psutil

    found = set()
    for process in psutil.process_iter(["name"]):
        name = (process.info.get("name") or "").lower()
        if name in DPI_TOOLS:
            found.add(DPI_TOOLS[name])
    return sorted(found)


class WindowsPlatform(BasePlatform):
    name = "windows"
    supports_firewall = True
    supports_autostart = True
    supports_elevation = True
    supports_icons = True

    def __init__(self) -> None:
        super().__init__()
        from agnabzi.windows.icons import IconCache

        self._icons = IconCache()

    def _require_admin(self) -> None:
        if not self.admin:
            raise PlatformError("not_admin")

    def block(self, exe: str) -> None:
        from agnabzi.windows import firewall

        self._require_admin()
        try:
            firewall.block(exe, sys.executable)
        except firewall.FirewallError as exc:
            raise PlatformError(exc.code) from exc

    def unblock(self, exe: str) -> None:
        from agnabzi.windows import firewall

        self._require_admin()
        try:
            firewall.unblock(exe)
        except firewall.FirewallError as exc:
            raise PlatformError(exc.code) from exc

    def reveal(self, exe: str) -> None:
        from agnabzi.windows import api

        if not exe or not os.path.exists(exe):
            raise PlatformError("not_found")
        api.reveal_in_explorer(exe)

    def icon(self, exe: str) -> bytes | None:
        return self._icons.get(exe) if exe else None

    def autostart_status(self) -> dict[str, Any]:
        from agnabzi.windows import autostart

        return autostart.status()

    def set_autostart(self, enabled: bool) -> dict[str, Any]:
        from agnabzi.windows import autostart

        if enabled:
            executable, args = launch_command(["--background"])
            autostart.enable(executable, args, elevated=self.admin)
        else:
            autostart.disable()
        return autostart.status()

    def elevate(self, args: list[str]) -> bool:
        from agnabzi.windows import api

        executable, arguments = launch_command(args)
        return api.run_elevated(executable, arguments, cwd=project_root())

    def dpi_status(self) -> dict[str, Any]:
        try:
            tools = _running_dpi_tools()
        except Exception:
            tools = []
        return {"supported": True, "active": bool(tools), "tools": tools}

    def shutdown(self) -> None:
        self._icons.shutdown()


class DemoPlatform(BasePlatform):
    name = "demo"
    supports_firewall = True
    supports_autostart = True

    def __init__(self) -> None:
        super().__init__()
        self.admin = True
        self._autostart = False

    def block(self, exe: str) -> None:
        if not exe:
            raise PlatformError("invalid_path")

    def unblock(self, exe: str) -> None:
        pass

    def reveal(self, exe: str) -> None:
        pass

    def autostart_status(self) -> dict[str, Any]:
        return {"enabled": self._autostart, "mode": "task" if self._autostart else None}

    def set_autostart(self, enabled: bool) -> dict[str, Any]:
        self._autostart = enabled
        return self.autostart_status()

    def dpi_status(self) -> dict[str, Any]:
        return {"supported": True, "active": True, "tools": ["GoodbyeDPI"]}


def create_platform(demo: bool) -> BasePlatform:
    if demo:
        return DemoPlatform()
    if IS_WINDOWS:
        try:
            return WindowsPlatform()
        except OSError:
            log.exception("Windows integrations unavailable")
    return BasePlatform()
