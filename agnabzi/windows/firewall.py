"""Per-application internet blocking through Windows Defender Firewall rules."""

from __future__ import annotations

import hashlib
import ntpath
import subprocess

RULE_PREFIX = "NetPulse"
CREATE_NO_WINDOW = 0x08000000

PROTECTED_EXECUTABLES = {
    "csrss.exe", "dwm.exe", "explorer.exe", "lsass.exe", "services.exe", "smss.exe", "svchost.exe",
    "system", "wininit.exe", "winlogon.exe", "msmpeng.exe", "searchhost.exe", "spoolsv.exe",
    "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe", "runtimebroker.exe",
    "dllhost.exe", "conhost.exe", "wudfhost.exe", "audiodg.exe", "securityhealthservice.exe",
}

# Blocking anything Windows ships from its own directory can break the OS or its
# updates, so the whole tree is off limits regardless of the file name.
PROTECTED_DIRS = ("c:\\windows\\",)


class FirewallError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def rule_name(exe: str) -> str:
    digest = hashlib.sha1(exe.lower().encode("utf-8")).hexdigest()[:8]
    return f"{RULE_PREFIX} block {ntpath.basename(exe)} {digest}"


def add_rule_command(name: str, exe: str, direction: str) -> str:
    return (
        f'netsh advfirewall firewall add rule name="{name}" dir={direction} action=block '
        f'program="{exe}" enable=yes profile=any'
    )


def delete_rule_command(name: str) -> str:
    return f'netsh advfirewall firewall delete rule name="{name}"'


def show_rule_command(name: str) -> str:
    return f'netsh advfirewall firewall show rule name="{name}"'


def ensure_blockable(exe: str, own_executable: str) -> None:
    if not exe or not ntpath.isabs(exe) or '"' in exe:
        raise FirewallError("invalid_path")
    if ntpath.basename(exe).lower() in PROTECTED_EXECUTABLES:
        raise FirewallError("protected")
    if ntpath.normcase(exe).startswith(PROTECTED_DIRS):
        raise FirewallError("protected")
    if ntpath.normcase(exe) == ntpath.normcase(own_executable):
        raise FirewallError("protected")


def _run(command: str) -> subprocess.CompletedProcess:
    # netsh parses its own command line, so it is passed through verbatim.
    return subprocess.run(
        command, capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW, check=False
    )


def rule_exists(name: str) -> bool:
    return _run(show_rule_command(name)).returncode == 0


def block(exe: str, own_executable: str) -> str:
    ensure_blockable(exe, own_executable)
    name = rule_name(exe)
    _run(delete_rule_command(name))
    for direction in ("out", "in"):
        result = _run(add_rule_command(name, exe, direction))
        if result.returncode != 0:
            _run(delete_rule_command(name))
            raise FirewallError("failed", result.stdout.decode(errors="replace").strip())
    return name


def unblock(exe: str) -> None:
    name = rule_name(exe)
    if rule_exists(name) and _run(delete_rule_command(name)).returncode != 0:
        raise FirewallError("failed")
