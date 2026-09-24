"""Minimal PNG encoder for RGBA pixel buffers (used for application icons)."""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_rgba(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    if len(rgba) != stride * height:
        raise ValueError("pixel buffer does not match the image size")
    scanlines = b"".join(b"\x00" + rgba[row * stride : (row + 1) * stride] for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return _SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(scanlines, 9)) + _chunk(b"IEND", b"")


def bgra_to_rgba(bgra: bytes) -> bytes:
    rgba = bytearray(bgra)
    rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]
    return bytes(rgba)
