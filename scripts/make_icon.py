"""Render the application icon (ICO for Windows, PNG for docs) from vector shapes."""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "agnabzi", "web", "static", "img")
SIZE = 1024


def render(size: int = SIZE) -> Image.Image:
    scale = size / 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (size, size))
    top, bottom = (47, 128, 228), (27, 79, 156)
    pixels = gradient.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            pixels[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(15 * scale), fill=255)
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    points = [(8, 36), (18, 36), (23, 23), (31, 47), (38, 28), (42, 36), (52, 36)]
    scaled = [(x * scale, y * scale) for x, y in points]
    width = int(5 * scale)
    draw.line(scaled, fill="white", width=width, joint="curve")
    radius = width / 2
    for x, y in (scaled[0], scaled[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="white")
    cx, cy, r = 56 * scale, 36 * scale, 4 * scale
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 122, 69, 255))
    return image


def main() -> int:
    os.makedirs(TARGET, exist_ok=True)
    master = render()
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    master.resize((256, 256), Image.LANCZOS).save(
        os.path.join(TARGET, "netpulse.ico"), sizes=[(s, s) for s in sizes]
    )
    master.resize((256, 256), Image.LANCZOS).save(os.path.join(TARGET, "netpulse-256.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
