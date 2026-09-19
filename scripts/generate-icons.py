#!/usr/bin/env python3
"""Generate FaithMap home-screen icons — dark panel + six religion dots."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
BG = (10, 12, 16)
PANEL = (20, 24, 32)
DOTS = [
    (215, 196, 163),  # christian
    (122, 162, 255),  # jewish
    (61, 186, 122),   # muslim
    (224, 122, 61),   # hindu
    (224, 194, 92),   # buddhist
    (240, 138, 31),   # sikh
]


def build_icon(size: int, maskable: bool = False) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (*BG, 255))
    draw = ImageDraw.Draw(canvas)
    pad = int(size * (0.18 if maskable else 0.1))
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=int(size * 0.12),
        fill=(*PANEL, 255),
        outline=(255, 255, 255, 28),
        width=max(1, size // 128),
    )
    cx = cy = size / 2
    ring = size * 0.22
    r = max(3, int(size * 0.055))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i, color in enumerate(DOTS):
        ang = -math.pi / 2 + i * (2 * math.pi / 6)
        x = cx + ring * math.cos(ang)
        y = cy + ring * math.sin(ang)
        gd.ellipse((x - r * 2, y - r * 2, x + r * 2, y + r * 2), fill=(*color, 50))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*color, 255))
    canvas = Image.alpha_composite(
        canvas, glow.filter(ImageFilter.GaussianBlur(radius=max(1, size // 28)))
    )
    return canvas


def save(img: Image.Image, name: str) -> None:
    path = ROOT / name
    img.convert("RGB").save(path, "PNG", optimize=True)
    print("wrote", path)


def main() -> None:
    save(build_icon(192), "icon-192.png")
    save(build_icon(512), "icon-512.png")
    save(build_icon(512, maskable=True), "icon-maskable-512.png")
    save(build_icon(180), "apple-touch-icon.png")
    save(build_icon(180), "apple-touch-icon-180x180.png")
    img32 = build_icon(64).resize((32, 32), Image.Resampling.LANCZOS)
    save(img32, "favicon-32.png")


if __name__ == "__main__":
    main()
