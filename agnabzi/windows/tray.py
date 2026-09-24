"""Notification-area icon with a context menu and balloon notifications."""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_NULL = 0x0000
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_REFRESH = WM_APP + 2
NIN_BALLOONUSERCLICK = 0x0405

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0, 0x800, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
IDI_APPLICATION = 32512


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = LRESULT
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
_user32.LoadImageW.restype = wintypes.HANDLE
_user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
_user32.LoadIconW.restype = wintypes.HICON
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
_user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
_user32.DestroyMenu.argtypes = [wintypes.HMENU]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
_shell32.Shell_NotifyIconW.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: Callable[[], None] | None = None
    enabled: bool = True


SEPARATOR = MenuItem("-")


class TrayIcon:
    """Runs its own message loop thread; every public method is thread-safe."""

    CLASS_NAME = "NetPulseTrayWindow"

    def __init__(
        self,
        tooltip: str,
        icon_path: str | None,
        menu: Callable[[], list[MenuItem]],
        on_activate: Callable[[], None],
        on_session_end: Callable[[], None],
    ):
        self._tooltip = tooltip
        self._icon_path = icon_path
        self._menu = menu
        self._on_activate = on_activate
        self._on_session_end = on_session_end
        self._hwnd = None
        self._icon = None
        self._balloon: tuple[str, str, bool] | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._wndproc = WNDPROC(self._window_proc)
        self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)

    def start(self, timeout: float = 5.0) -> bool:
        self._thread.start()
        return self._ready.wait(timeout) and self._hwnd is not None

    def set_tooltip(self, text: str) -> None:
        with self._lock:
            self._tooltip = text[:127]
        self._post(WM_REFRESH)

    def notify(self, title: str, message: str, warning: bool = False) -> None:
        with self._lock:
            self._balloon = (title[:63], message[:255], warning)
        self._post(WM_REFRESH)

    def stop(self) -> None:
        self._post(WM_CLOSE)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=3)

    def _post(self, message: int) -> None:
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, message, 0, 0)

    def _run(self) -> None:
        try:
            instance = _kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=instance, lpszClassName=self.CLASS_NAME)
            _user32.RegisterClassW(ctypes.byref(window_class))
            self._hwnd = _user32.CreateWindowExW(
                0, self.CLASS_NAME, "NetPulse", 0, 0, 0, 0, 0, None, None, instance, None
            )
            if not self._hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._icon = self._load_icon()
            self._add_icon()
        except OSError:
            log.exception("tray icon unavailable")
            self._hwnd = None
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))

    def _load_icon(self):
        if self._icon_path:
            icon = _user32.LoadImageW(None, self._icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if icon:
                return icon
        return _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _base_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        return data

    def _add_icon(self) -> None:
        data = self._base_data()
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._tooltip
        _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data))

    def _refresh(self) -> None:
        with self._lock:
            tooltip, balloon, self._balloon = self._tooltip, self._balloon, None
        data = self._base_data()
        data.uFlags = NIF_TIP
        data.szTip = tooltip
        if balloon:
            title, message, warning = balloon
            data.uFlags |= NIF_INFO
            data.szInfoTitle = title
            data.szInfo = message
            data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _show_menu(self) -> None:
        items = self._menu()
        menu = _user32.CreatePopupMenu()
        try:
            for index, item in enumerate(items, start=1):
                if item is SEPARATOR or item.label == "-":
                    _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    flags = MF_STRING | (0 if item.enabled and item.action else MF_GRAYED)
                    _user32.AppendMenuW(menu, flags, index, item.label)
            point = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            _user32.SetForegroundWindow(self._hwnd)
            chosen = _user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, point.x, point.y, 0, self._hwnd, None
            )
            _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            _user32.DestroyMenu(menu)
        if chosen and items[chosen - 1].action:
            self._safely(items[chosen - 1].action)

    @staticmethod
    def _safely(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            log.exception("tray action failed")

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_BALLOONUSERCLICK):
                self._safely(self._on_activate)
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == WM_REFRESH:
            self._refresh()
            return 0
        if message == self._taskbar_created:
            self._add_icon()
            return 0
        if message == WM_QUERYENDSESSION:
            return 1
        if message == WM_ENDSESSION:
            if wparam:
                self._safely(self._on_session_end)
            return 0
        if message == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._base_data()))
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)
