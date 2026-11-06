"""Colour, quantized to whatever the terminal can show.

Every function takes the capability level and returns plain text at
``PLAIN``, so callers never branch on it. RGB in, the nearest the terminal
has out: 24-bit as is, the xterm 6x6x6 cube and grey ramp at 256, the
16-colour CGA set at 16.
"""

from __future__ import annotations

import colorsys
import math
import re
from collections.abc import Sequence

from .caps import C256, PLAIN, TRUECOLOR

RGB = tuple[int, int, int]
RESET = "\033[0m"
BOLD = "\033[1m"
_ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

# The site's palette: DOS/CGA plus amber phosphor and a near-black ink.
INK: RGB = (5, 3, 12)
AMBER: RGB = (255, 176, 0)
MAGENTA: RGB = (255, 85, 255)
CYAN: RGB = (85, 255, 255)
GREEN: RGB = (85, 255, 85)
YELLOW: RGB = (255, 255, 85)
RED: RGB = (255, 85, 85)
BLUE: RGB = (85, 85, 255)
NAVY: RGB = (0, 0, 170)
TEAL: RGB = (0, 170, 170)
GREY: RGB = (170, 170, 170)
WHITE: RGB = (255, 255, 255)
SILVER: RGB = (192, 192, 192)

# ANSI order: black red green yellow blue magenta cyan white, then bright.
CGA16: list[RGB] = [
    (0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0),
    (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170),
    (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85),
    (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255),
]  # fmt: skip
_CUBE = (0, 95, 135, 175, 215, 255)


def visible_len(text: str) -> int:
    """Length on screen: escape sequences take no columns."""
    return len(_ANSI_RE.sub("", text))


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _dist(a: RGB, b: RGB) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _nearest16(rgb: RGB) -> int:
    return min(range(16), key=lambda i: _dist(rgb, CGA16[i]))


def _cube_index(v: int) -> int:
    return min(range(6), key=lambda i: abs(_CUBE[i] - v))


def _nearest256(rgb: RGB) -> int:
    r, g, b = (_cube_index(v) for v in rgb)
    cube = 16 + 36 * r + 6 * g + b
    cube_rgb = (_CUBE[r], _CUBE[g], _CUBE[b])
    grey_level = max(0, min(23, round((sum(rgb) / 3 - 8) / 10)))
    grey_v = 8 + 10 * grey_level
    if _dist(rgb, (grey_v,) * 3) < _dist(rgb, cube_rgb):
        return 232 + grey_level
    return cube


def code(rgb: RGB, level: int, *, background: bool = False) -> str:
    """The SGR escape that sets ``rgb`` as foreground (or background)."""
    if level == PLAIN:
        return ""
    if level == TRUECOLOR:
        return f"\033[{48 if background else 38};2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    if level == C256:
        return f"\033[{48 if background else 38};5;{_nearest256(rgb)}m"
    i = _nearest16(rgb)
    base = (40 if background else 30) if i < 8 else (100 if background else 90)
    return f"\033[{base + i % 8}m"


def paint(text: str, rgb: RGB, level: int, *, bold: bool = False, bg: RGB | None = None) -> str:
    if level == PLAIN or not text:
        return text
    prefix = (BOLD if bold else "") + code(rgb, level)
    if bg is not None:
        prefix += code(bg, level, background=True)
    return f"{prefix}{text}{RESET}"


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def ramp(stops: Sequence[RGB], t: float) -> RGB:
    """A colour ``t`` (0..1, wrapping) of the way along ``stops``."""
    t = t % 1.0
    span = t * len(stops)
    i = int(span)
    return lerp(stops[i % len(stops)], stops[(i + 1) % len(stops)], span - i)


def hue(t: float, sat: float = 0.85, val: float = 1.0) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(t % 1.0, sat, val)
    return (round(r * 255), round(g * 255), round(b * 255))


def gradient(
    text: str,
    stops: Sequence[RGB],
    level: int,
    *,
    phase: float = 0.0,
    bold: bool = False,
    bg: RGB | None = None,
) -> str:
    """``text`` coloured left to right along ``stops``; spaces stay bare."""
    if level == PLAIN or not text:
        return text
    n = max(1, len(text))
    out: list[str] = [BOLD] if bold else []
    if bg is not None:
        out.append(code(bg, level, background=True))
    last = ""
    for i, ch in enumerate(text):
        if ch != " ":
            c = code(ramp(stops, phase + i / n), level)
            if c != last:  # 16/256 colour repeats a lot; send each change once
                out.append(c)
                last = c
        out.append(ch)
    out.append(RESET)
    return "".join(out)


def wave(i: int, t: float, speed: float = 1.0, freq: float = 0.35) -> float:
    """A 0..1 sine for column ``i`` at time ``t``: the shimmer under everything."""
    return 0.5 + 0.5 * math.sin(i * freq + t * speed)
