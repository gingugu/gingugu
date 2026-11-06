"""Demoscene effects as pure functions of time.

Each effect takes the terminal's caps and a time ``t`` in seconds and returns
whole lines, so the player can draw any of them at any frame rate and a test
can check a frame without a terminal. With Unicode, a line of ``▀`` carries
two pixel rows (foreground on top, background below), doubling the vertical
resolution; without it, each line is one row of coloured background.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from . import art
from . import paint as p
from .caps import Caps

NEON = [p.MAGENTA, p.CYAN, p.AMBER, p.GREEN, p.BLUE]
COPPER = [(40, 10, 0), (170, 85, 0), (255, 176, 0), (255, 240, 180), (255, 176, 0), (170, 85, 0)]
Pixel = Callable[[int, int], p.RGB]


def _pixels(caps: Caps, width: int, lines: int, pixel: Pixel) -> list[str]:
    """Render ``pixel(x, y)`` into ``lines`` terminal lines ``width`` wide."""
    lvl = caps.color
    out = []
    for line in range(lines):
        cells = []
        last = ""
        for x in range(width):
            if caps.unicode:
                top, bot = pixel(x, line * 2), pixel(x, line * 2 + 1)
                c = p.code(top, lvl) + p.code(bot, lvl, background=True)
                cells.append(("" if c == last else c) + "▀")
            else:
                c = p.code(pixel(x, line), lvl, background=True)
                cells.append(("" if c == last else c) + " ")
            last = c
        out.append("".join(cells) + p.RESET)
    return out


def plasma(caps: Caps, t: float, width: int, lines: int = 3) -> list[str]:
    """The classic: four sines summed and pushed through the neon ramp."""

    def pixel(x: int, y: int) -> p.RGB:
        v = math.sin(x * 0.16 + t * 2.1) + math.sin(y * 0.5 - t * 1.3)
        v += math.sin((x * 0.08 + y * 0.3) + t) + math.sin(math.hypot(x - width / 2, y * 3) * 0.12)
        return p.ramp(NEON, v / 8 + 0.5 + t * 0.05)

    return _pixels(caps, width, lines, pixel)


def rasterbars(caps: Caps, t: float, width: int, lines: int = 3, bars: int = 3) -> list[str]:
    """Copper bars bouncing on sines, like every intro in 1992."""
    rows = lines * (2 if caps.unicode else 1)
    centres = [(rows - 1) * (0.5 + 0.5 * math.sin(t * 2.4 + k * 2.1)) for k in range(bars)]

    def pixel(x: int, y: int) -> p.RGB:
        best = p.INK
        for k, cy in enumerate(centres):
            d = abs(y - cy)
            if d < 1.6:
                shade = p.ramp(COPPER, 0.5 - d / 4) if k == 0 else p.hue(k / bars + t * 0.1)
                best = p.lerp(shade, p.INK, d / 1.6)
        return best

    return _pixels(caps, width, lines, pixel)


def logo(caps: Caps, t: float, width: int) -> list[str]:
    """GINGUGU in blocks, a plasma shimmer running through the letters."""
    rows = art.logo_rows(unicode=caps.unicode)
    if width < art.LOGO_WIDTH:  # too narrow for blocks: the spaced word, still shimmering
        rows = ["", "", "G I N G U G U", "", ""]
    out = []
    for r, row in enumerate(rows):
        if caps.plain:
            out.append(art.center(row, width))
            continue
        cells = []
        for i, ch in enumerate(row):
            if ch == " ":
                cells.append(" ")
                continue
            phase = p.wave(i, t, speed=3.0, freq=0.18) * 0.6 + r * 0.06 + t * 0.15
            cells.append(p.code(p.ramp(NEON, phase), caps.color) + ch)
        out.append(art.center(p.BOLD + "".join(cells) + p.RESET, width))
    return out


def scroller(caps: Caps, text: str, t: float, width: int, lines: int = 3) -> list[str]:
    """A sine scroller: the text slides left, every letter riding the wave."""
    speed = 22.0  # columns per second
    offset = int(t * speed) % (len(text) + width)
    grid = [[" "] * width for _ in range(lines)]
    colours: dict[tuple[int, int], p.RGB] = {}
    for i, ch in enumerate(text):
        x = width - offset + i
        if not 0 <= x < width or ch == " ":
            continue
        y = round((lines - 1) * (0.5 + 0.5 * math.sin(x * 0.22 + t * 4)))
        grid[y][x] = ch
        colours[(y, x)] = p.hue(i / 24 + t * 0.3)
    out = []
    for y, row in enumerate(grid):
        if caps.plain:
            out.append("".join(row))
            continue
        out.append(
            "".join(
                p.code(colours[(y, x)], caps.color) + ch if (y, x) in colours else ch
                for x, ch in enumerate(row)
            )
            + p.RESET
        )
    return out


def progress(caps: Caps, label: str, frac: float, t: float, width: int) -> str:
    """``LABEL [####----] 63% ~`` with a shimmering fill and a bouncing note."""
    g = art.glyphs(caps)
    frac = max(0.0, min(1.0, frac))
    pct = f"{round(frac * 100):3d}%"
    bar_w = max(8, width - len(label) - len(pct) - 6)
    filled = round(bar_w * frac)
    note = g.note if int(t * 8) % 2 else " "
    if caps.plain:
        return f"{label} [{'#' * filled}{'-' * (bar_w - filled)}] {pct}"
    fill = "".join(
        p.code(p.ramp(NEON, i / bar_w * 0.5 + t * 0.8), caps.color) + g.full for i in range(filled)
    )
    rest = p.paint(g.empty * (bar_w - filled), p.GREY, caps.color)
    head = p.paint(label, p.AMBER, caps.color, bold=True)
    tail = p.paint(pct, p.WHITE, caps.color, bold=True) + " " + p.paint(note, p.MAGENTA, caps.color)
    return f"{head} [{fill}{p.RESET}{rest}] {tail}"
