#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
"""Paint boot logo onto framebuffer device.

Kernel logo only reaches firmware framebuffer. DRM fbdevs register after boot
logo data is freed, so fbcon leaves them black. udev starts camlab-splash@fbN
when fbdev appears. Logo centered, border filled from top-left pixel.

--progress and --label add a bar and a status line under the logo, repainted per
step by camlab.updater on an update boot, where no compositor runs yet. Bar
proportions follow the cinepi plymouth theme.

Usage: fbsplash.py /dev/fbN [logo.tga] [--progress 0..1] [--label TEXT]
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LOGO = "/lib/firmware/logo.tga"
# splash.sh installs the face beside this script. Second is a repo checkout.
FONT_DIRS = (Path(__file__).resolve().parent, Path(__file__).resolve().parents[2] / "camlab/assets")
FONT_NAME = "Roboto-Regular.ttf"
TRACK = (51, 51, 51)
INK = (255, 255, 255)
BAR_H = 6


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


def place(logo: np.ndarray, width: int, height: int) -> tuple[int, int, int, int]:
    """Centered logo box as x, y, w, h, clipped to the canvas."""
    lh, lw = logo.shape[:2]
    h, w = min(lh, height), min(lw, width)
    return (width - w) // 2, (height - h) // 2, w, h


def compose(logo: np.ndarray, width: int, height: int) -> np.ndarray:
    """Center logo on a canvas filled with its corner color, clip overflow."""
    canvas = np.empty((height, width, 3), np.uint8)
    canvas[:] = logo[0, 0]
    x, y, w, h = place(logo, width, height)
    lh, lw = logo.shape[:2]
    top, left = (lh - h) // 2, (lw - w) // 2
    canvas[y : y + h, x : x + w] = logo[top : top + h, left : left + w]
    return canvas


def block_top(box: tuple[int, int, int, int]) -> int:
    """Status block clears the wordmark by half its height."""
    _, y, _, h = box
    return y + h + h // 2


def font_file() -> Path | None:
    return next((d / FONT_NAME for d in FONT_DIRS if (d / FONT_NAME).is_file()), None)


def draw_label(canvas: np.ndarray, box: tuple[int, int, int, int], top: int, text: str) -> int:
    """Status line centered at top, blended so glyph edges stay smooth. Returns its bottom.

    No font drops the line and leaves the bar in its place.
    """
    path = font_file()
    if path is None:
        return top
    size = max(10, int(box[3] * 0.2))
    mask = Image.new("L", canvas.shape[1::-1], 0)
    ImageDraw.Draw(mask).text(
        (canvas.shape[1] // 2, top),
        text,
        font=ImageFont.truetype(str(path), size),
        fill=255,
        anchor="ma",
    )
    alpha = np.asarray(mask, np.float32)[..., None] / 255.0
    np.copyto(
        canvas, (canvas * (1.0 - alpha) + np.asarray(INK, np.float32) * alpha).astype(np.uint8)
    )
    return top + 2 * size


def draw_bar(canvas: np.ndarray, box: tuple[int, int, int, int], top: int, fraction: float) -> None:
    """Fill a thin bar at top, width taken from the wordmark."""
    bar_w = int(box[2] * 0.55)
    bar_h = min(BAR_H, canvas.shape[0])
    x = (canvas.shape[1] - bar_w) // 2
    top = min(top, canvas.shape[0] - bar_h)
    canvas[top : top + bar_h, x : x + bar_w] = TRACK
    canvas[top : top + bar_h, x : x + int(bar_w * min(max(fraction, 0.0), 1.0))] = INK


def render(
    logo: np.ndarray, width: int, height: int, fraction: float | None, label: str
) -> np.ndarray:
    canvas = compose(logo, width, height)
    if fraction is None:
        return canvas
    box = place(logo, width, height)
    top = block_top(box)
    if label:
        top = draw_label(canvas, box, top, label)
    draw_bar(canvas, box, top, fraction)
    return canvas


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


def _take(args: list[str], flag: str) -> str | None:
    if flag not in args:
        return None
    i = args.index(flag)
    value = args[i + 1]
    del args[i : i + 2]
    return value


def main() -> None:
    args = sys.argv[1:]
    progress = _take(args, "--progress")
    label = _take(args, "--label") or ""
    fbdev = args[0]
    logo = load_tga(args[1] if len(args) > 1 else LOGO)

    sys_dir = Path("/sys/class/graphics") / Path(fbdev).name
    width, height = map(int, (sys_dir / "virtual_size").read_text().split(","))
    bpp = int((sys_dir / "bits_per_pixel").read_text())
    stride = int((sys_dir / "stride").read_text())

    canvas = render(logo, width, height, float(progress) if progress else None, label)
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
