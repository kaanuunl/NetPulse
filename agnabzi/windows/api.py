"""ctypes bindings for the handful of Win32 calls the application relies on."""

from __future__ import annotations

import ctypes
import socket
import struct
import subprocess
import time
from ctypes import wintypes
from typing import Callable, NamedTuple

_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
_version = ctypes.WinDLL("version", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_SHOWNORMAL = 1
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

_ShellExecuteW = _shell32.ShellExecuteW
_ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
]
_ShellExecuteW.restype = wintypes.HINSTANCE


def shell_execute(target: str, args: list[str] | None = None, verb: str = "open", cwd: str | None = None) -> bool:
    params = subprocess.list2cmdline(args) if args else None
    result = _ShellExecuteW(None, verb, target, params, cwd, SW_SHOWNORMAL)
    return (result or 0) > 32


def run_elevated(executable: str, args: list[str], cwd: str | None = None) -> bool:
    """Start a process through the UAC prompt. False when the user declines."""
    return shell_execute(executable, args, verb="runas", cwd=cwd)


def reveal_in_explorer(path: str) -> bool:
    return shell_execute("explorer.exe", [f"/select,{path}"])


# --- version resources --------------------------------------------------------

_GetFileVersionInfoSizeW = _version.GetFileVersionInfoSizeW
_GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_GetFileVersionInfoSizeW.restype = wintypes.DWORD

_GetFileVersionInfoW = _version.GetFileVersionInfoW
_GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
_GetFileVersionInfoW.restype = wintypes.BOOL

_VerQueryValueW = _version.VerQueryValueW
_VerQueryValueW.argtypes = [
    wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
]
_VerQueryValueW.restype = wintypes.BOOL

_FALLBACK_TRANSLATIONS = [(0x0409, 0x04B0), (0x0409, 0x04E4), (0x0000, 0x04B0)]


def file_description(path: str) -> str:
    """Return the FileDescription string embedded in an executable, if any."""
    if not path:
        return ""
    size = _GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    block = ctypes.create_string_buffer(size)
    if not _GetFileVersionInfoW(path, 0, size, block):
        return ""
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    translations: list[tuple[int, int]] = []
    if _VerQueryValueW(block, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)):
        pairs = (wintypes.WORD * (length.value // 2)).from_address(pointer.value)
        translations = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs) - 1, 2)]
    for lang, codepage in translations + _FALLBACK_TRANSLATIONS:
        query = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
        if _VerQueryValueW(block, query, ctypes.byref(pointer), ctypes.byref(length)) and length.value > 1:
            text = ctypes.wstring_at(pointer.value).strip()
            if text:
                return text
    return ""


# --- routing ------------------------------------------------------------------


class MIB_IPFORWARDROW(ctypes.Structure):
    _fields_ = [
        (name, wintypes.DWORD)
        for name in (
            "dwForwardDest", "dwForwardMask", "dwForwardPolicy", "dwForwardNextHop", "dwForwardIfIndex",
            "dwForwardType", "dwForwardProto", "dwForwardAge", "dwForwardNextHopAS",
            "dwForwardMetric1", "dwForwardMetric2", "dwForwardMetric3", "dwForwardMetric4", "dwForwardMetric5",
        )
    ]


_GetBestRoute = _iphlpapi.GetBestRoute
_GetBestRoute.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(MIB_IPFORWARDROW)]
_GetBestRoute.restype = wintypes.DWORD


def _ipv4_to_dword(ip: str) -> int:
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def _dword_to_ipv4(value: int) -> str:
    return socket.inet_ntoa(struct.pack("<I", value))


def best_route_gateway(probe: str = "8.8.8.8") -> str | None:
    row = MIB_IPFORWARDROW()
    if _GetBestRoute(_ipv4_to_dword(probe), 0, ctypes.byref(row)) != 0:
        return None
    return _dword_to_ipv4(row.dwForwardNextHop) if row.dwForwardNextHop else None


# --- ICMP ---------------------------------------------------------------------


class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", ctypes.c_ulong),
        ("Status", ctypes.c_ulong),
        ("RoundTripTime", ctypes.c_ulong),
        ("DataSize", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


_IcmpCreateFile = _iphlpapi.IcmpCreateFile
_IcmpCreateFile.argtypes = []
_IcmpCreateFile.restype = wintypes.HANDLE

_IcmpCloseHandle = _iphlpapi.IcmpCloseHandle
_IcmpCloseHandle.argtypes = [wintypes.HANDLE]
_IcmpCloseHandle.restype = wintypes.BOOL

_IcmpSendEcho = _iphlpapi.IcmpSendEcho
_IcmpSendEcho.argtypes = [
    wintypes.HANDLE, ctypes.c_ulong, wintypes.LPVOID, wintypes.WORD,
    ctypes.POINTER(IP_OPTION_INFORMATION), wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
]
_IcmpSendEcho.restype = wintypes.DWORD

IP_SUCCESS = 0
IP_REQ_TIMED_OUT = 11010
IP_TTL_EXPIRED_TRANSIT = 11013
IP_TTL_EXPIRED_REASSEM = 11014
_UNREACHABLE = {11002, 11003, 11004, 11005}
_PAYLOAD = b"NetPulse.latency.probe.32bytes..."


class EchoResult(NamedTuple):
    status: str
    address: str | None
    rtt_ms: float | None


class IcmpPinger:
    """ICMP echo through iphlpapi, which works without administrator rights."""

    def __init__(self) -> None:
        self._handle = _IcmpCreateFile()
        if not self._handle or self._handle == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        self._reply_size = ctypes.sizeof(ICMP_ECHO_REPLY) + len(_PAYLOAD) + 64
        self._payload = ctypes.create_string_buffer(_PAYLOAD, len(_PAYLOAD))

    def echo(self, ip: str, timeout_ms: int = 1000, ttl: int | None = None) -> EchoResult:
        reply_buffer = ctypes.create_string_buffer(self._reply_size)
        options = ctypes.byref(IP_OPTION_INFORMATION(Ttl=ttl)) if ttl else None
        started = time.perf_counter()
        count = _IcmpSendEcho(
            self._handle, _ipv4_to_dword(ip), self._payload, len(_PAYLOAD),
            options, reply_buffer, self._reply_size, timeout_ms,
        )
        elapsed = (time.perf_counter() - started) * 1000
        error = 0 if count else ctypes.get_last_error()
        if not count and error in (0, IP_REQ_TIMED_OUT):
            return EchoResult("timeout", None, None)
        reply = ICMP_ECHO_REPLY.from_buffer(reply_buffer)
        status = reply.Status if count else error
        responder = _dword_to_ipv4(reply.Address) if reply.Address else None
        rtt = float(reply.RoundTripTime) if reply.RoundTripTime else min(elapsed, 0.9)
        if status == IP_SUCCESS:
            return EchoResult("ok", responder, rtt)
        if status in (IP_TTL_EXPIRED_TRANSIT, IP_TTL_EXPIRED_REASSEM):
            return EchoResult("ttl_expired", responder, rtt)
        if status in _UNREACHABLE:
            return EchoResult("unreachable", responder, rtt)
        if status == IP_REQ_TIMED_OUT:
            return EchoResult("timeout", None, None)
        return EchoResult("error", responder, None)

    def close(self) -> None:
        if self._handle:
            _IcmpCloseHandle(self._handle)
            self._handle = None

    def __del__(self) -> None:
        self.close()


# --- console ------------------------------------------------------------------

_HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
_console_handlers: list = []
CTRL_CLOSE_EVENT = 2
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6


def on_console_close(callback: Callable[[], None]) -> None:
    """Run ``callback`` synchronously when the console window is closed."""

    def handler(event: int) -> bool:
        if event in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            callback()
            return True
        return False

    routine = _HandlerRoutine(handler)
    _kernel32.SetConsoleCtrlHandler(routine, True)
    _console_handlers.append(routine)


_kernel32.GetConsoleWindow.restype = wintypes.HWND


def has_console() -> bool:
    return bool(_kernel32.GetConsoleWindow())
