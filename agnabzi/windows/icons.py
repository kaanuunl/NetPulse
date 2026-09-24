"""Extract executable icons as PNG images for the dashboard."""

from __future__ import annotations

import ctypes
import threading
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

from agnabzi.png import bgra_to_rgba, encode_rgba

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
DIB_RGB_COLORS = 0
COINIT_APARTMENTTHREADED = 0x2

_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32", use_last_error=True)


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", wintypes.LPVOID),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 256)]


_shell32.SHGetFileInfoW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT,
]
_shell32.SHGetFileInfoW.restype = ctypes.c_size_t
_user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
_user32.GetIconInfo.restype = wintypes.BOOL
_user32.DestroyIcon.argtypes = [wintypes.HICON]
_gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, wintypes.LPVOID,
    ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]
_ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]


def _read_bitmap(dc: int, bitmap: int, width: int, height: int) -> bytes:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    pixels = ctypes.create_string_buffer(width * height * 4)
    if not _gdi32.GetDIBits(dc, bitmap, 0, height, pixels, ctypes.byref(info), DIB_RGB_COLORS):
        raise OSError("GetDIBits failed")
    return pixels.raw


def _icon_to_png(icon: int) -> bytes | None:
    info = ICONINFO()
    if not _user32.GetIconInfo(icon, ctypes.byref(info)):
        return None
    dc = _gdi32.CreateCompatibleDC(None)
    try:
        if not info.hbmColor:
            return None
        bitmap = BITMAP()
        _gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bitmap))
        width, height = bitmap.bmWidth, bitmap.bmHeight
        bgra = bytearray(_read_bitmap(dc, info.hbmColor, width, height))
        if not any(bgra[3::4]):
            mask = _read_bitmap(dc, info.hbmMask, width, height)
            bgra[3::4] = bytes(0 if mask[i] else 255 for i in range(0, len(mask), 4))
        return encode_rgba(width, height, bgra_to_rgba(bytes(bgra)))
    finally:
        _gdi32.DeleteDC(dc)
        if info.hbmColor:
            _gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            _gdi32.DeleteObject(info.hbmMask)


def _extract(path: str) -> bytes | None:
    info = SHFILEINFOW()
    if not _shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON):
        return None
    try:
        return _icon_to_png(info.hIcon)
    finally:
        _user32.DestroyIcon(info.hIcon)


class IconCache:
    """Extracts icons on one COM-initialised worker thread and keeps the results."""

    def __init__(self) -> None:
        self._cache: dict[str, bytes | None] = {}
        self._lock = threading.Lock()
        self._worker = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="icons",
            initializer=lambda: _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED),
        )

    def get(self, path: str, timeout: float = 3.0) -> bytes | None:
        key = path.lower()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        try:
            png = self._worker.submit(_extract, path).result(timeout)
        except Exception:
            png = None
        with self._lock:
            self._cache[key] = png
        return png

    def shutdown(self) -> None:
        self._worker.shutdown(wait=False, cancel_futures=True)
