"""Start the monitor at sign-in.

With administrator rights a scheduled task running at the highest privilege
level is used, so per-application statistics are available without a UAC
prompt on every boot. Otherwise the per-user Run key is used.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import tempfile
import winreg
from xml.sax.saxutils import escape

TASK_NAME = "NetPulse"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "NetPulse"
CREATE_NO_WINDOW = 0x08000000

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>NetPulse network monitor</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>PT15S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args], capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW, check=False
    )


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def _task_exists() -> bool:
    return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0


def _run_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except OSError:
        return None


def status() -> dict[str, object]:
    if _task_exists():
        return {"enabled": True, "mode": "task"}
    if _run_value():
        return {"enabled": True, "mode": "registry"}
    return {"enabled": False, "mode": None}


def enable(executable: str, arguments: list[str], elevated: bool) -> str:
    disable()
    arg_line = subprocess.list2cmdline(arguments)
    if elevated:
        xml = _TASK_XML.format(
            user=escape(_current_user()), command=escape(executable), arguments=escape(arg_line)
        )
        fd, path = tempfile.mkstemp(suffix=".xml")
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as fh:
                fh.write(xml)
            if _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F").returncode == 0:
                return "task"
        finally:
            os.unlink(path)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline([executable, *arguments]))
    return "registry"


def disable() -> None:
    if _task_exists():
        _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except OSError:
        pass
