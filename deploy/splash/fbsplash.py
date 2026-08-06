#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
"""Paint boot logo onto framebuffer device.

Kernel logo only reaches firmware framebuffer. DRM fbdevs register after boot
logo data is freed, so fbcon leaves them black. udev starts camlab-splash@fbN
when fbdev appears. Logo centered, border filled from top-left pixel.

--progress adds a bar under the logo, repainted per step by camlab.updater on an
update boot, where no compositor runs yet.

Usage: fbsplash.py /dev/fbN [logo.tga] [--progress 0..1]
"""

import sys
from pathlib import Path

import numpy as np

LOGO = "/lib/firmware/logo.tga"
BAR_COLOR = (202, 32, 49)  # Kurokesu red


def load_tga(path: str) -> np.ndarray:
    """Uncompressed 24-bit TGA to HxWx3 RGB array."""
    raw = Path(path).read_bytes()
    if raw[2] != 2 or raw[16] != 24:
        sys.exit(f"{path}: need uncompressed 24-bit TGA (type 2)")
    width = raw[12] | raw[13] << 8
    height = raw[14] | raw[15] << 8
    pixels = np.frombuffer(raw, np.uint8, width * height * 3, 18 + raw[0])
    pixels = pixels.reshape(height, width, 3)
    if not raw[17] & 0x20:  # TGA default is bottom-up row order
        pixels = pixels[::-1]
    return pixels[:, :, ::-1]  # BGR to RGB


def compose(logo: np.ndarray, width: int, height: int) -> np.ndarray:
    """Center logo on a canvas filled with its corner color, clip overflow."""
    canvas = np.empty((height, width, 3), np.uint8)
    canvas[:] = logo[0, 0]
    lh, lw = logo.shape[:2]
    ch, cw = min(lh, height), min(lw, width)
    src = logo[(lh - ch) // 2 : (lh - ch) // 2 + ch, (lw - cw) // 2 : (lw - cw) // 2 + cw]
    y, x = (height - ch) // 2, (width - cw) // 2
    canvas[y : y + ch, x : x + cw] = src
    return canvas


def draw_bar(canvas: np.ndarray, fraction: float) -> None:
    """Outline a bar under the logo and fill it left to right, in place."""
    height, width = canvas.shape[:2]
    bar_w, bar_h = width // 3, max(8, height // 60)
    x, y = (width - bar_w) // 2, min(int(height * 0.78), height - bar_h)
    canvas[y : y + bar_h, x : x + bar_w] = BAR_COLOR
    inner = canvas[y + 2 : y + bar_h - 2, x + 2 : x + bar_w - 2]
    inner[:] = canvas[0, 0]
    inner[:, : int(inner.shape[1] * min(max(fraction, 0.0), 1.0))] = BAR_COLOR


def pack(canvas: np.ndarray, bpp: int) -> bytes:
    """RGB canvas to fbdev pixel bytes (XRGB8888 or RGB565)."""
    if bpp == 32:
        frame = np.zeros((*canvas.shape[:2], 4), np.uint8)
        frame[..., 0] = canvas[..., 2]
        frame[..., 1] = canvas[..., 1]
        frame[..., 2] = canvas[..., 0]
        return frame.tobytes()
    if bpp == 16:
        r, g, b = (canvas[..., i].astype(np.uint16) for i in range(3))
        return ((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)).astype("<u2").tobytes()
    sys.exit(f"unsupported bits_per_pixel: {bpp}")


def main() -> None:
    args = sys.argv[1:]
    fraction = None
    if "--progress" in args:
        i = args.index("--progress")
        fraction = float(args[i + 1])
        del args[i : i + 2]
    fbdev = args[0]
    logo = load_tga(args[1] if len(args) > 1 else LOGO)

    sys_dir = Path("/sys/class/graphics") / Path(fbdev).name
    width, height = map(int, (sys_dir / "virtual_size").read_text().split(","))
    bpp = int((sys_dir / "bits_per_pixel").read_text())
    stride = int((sys_dir / "stride").read_text())

    canvas = compose(logo, width, height)
    if fraction is not None:
        draw_bar(canvas, fraction)
    data = pack(canvas, bpp)
    row_len = width * bpp // 8
    with open(fbdev, "wb") as fb:
        if stride == row_len:
            fb.write(data)
        else:
            for row in range(height):
                fb.seek(row * stride)
                fb.write(data[row * row_len : (row + 1) * row_len])


if __name__ == "__main__":
    main()
