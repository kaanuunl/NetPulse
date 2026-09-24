"""Process-wide platform facts and filesystem locations."""

from __future__ import annotations

import os
import sys

IS_WINDOWS = sys.platform == "win32"
FROZEN = bool(getattr(sys, "frozen", False))


def data_dir() -> str:
    override = os.environ.get("NETPULSE_HOME")
    if override:
        path = override
    elif IS_WINDOWS:
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NetPulse")
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        path = os.path.join(config_home, "netpulse")
    os.makedirs(path, exist_ok=True)
    return path


def package_dir() -> str:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return os.path.join(bundle, "agnabzi")
    return os.path.dirname(os.path.abspath(__file__))


def static_dir() -> str:
    return os.path.join(package_dir(), "web", "static")


def is_admin() -> bool:
    if IS_WINDOWS:
        import ctypes

        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def launch_command(extra_args: list[str]) -> tuple[str, list[str]]:
    """Return the executable and arguments that start this application again."""
    if FROZEN:
        return sys.executable, list(extra_args)
    executable = sys.executable
    if IS_WINDOWS and "--console" not in extra_args and executable.lower().endswith("python.exe"):
        windowless = executable[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(windowless):
            executable = windowless
    entry = os.path.join(project_root(), "run.py")
    if os.path.exists(entry):
        return executable, [entry, *extra_args]
    return executable, ["-m", "agnabzi", *extra_args]


def project_root() -> str:
    return os.path.dirname(package_dir())
