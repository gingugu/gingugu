"""The pixels: block logo, keygen window chrome, glyph sets.

Everything here has an ASCII twin, because a Windows console on code page 437,
a ``LANG=C`` shell or a serial line must still get a logo, not mojibake.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import paint as p
from .caps import Caps

_FONT = {
    "G": [" ████ ", "██    ", "██ ███", "██  ██", " ████ "],
    "I": ["████", " ██ ", " ██ ", " ██ ", "████"],
    "N": ["██  ██", "███ ██", "██████", "██ ███", "██  ██"],
    "U": ["██  ██", "██  ██", "██  ██", "██  ██", " ████ "],
}
LOGO_ROWS = 5


def logo_rows(word: str = "GINGUGU", *, unicode: bool = True) -> list[str]:
    rows = [" ".join(_FONT[ch][r] for ch in word) for r in range(LOGO_ROWS)]
    return rows if unicode else [r.replace("█", "#") for r in rows]


LOGO_WIDTH = len(logo_rows()[0])


@dataclass(frozen=True)
class Glyphs:
    tl: str
    tr: str
    bl: str
    br: str
    h: str
    v: str
    ml: str
    mr: str
    full: str
    shade: str
    empty: str
    note: str
    star: str
    arrow: str
    spin: str


UNICODE = Glyphs("╔", "╗", "╚", "╝", "═", "║", "╟", "╢", "█", "▓", "░", "♫", "★", "►", "◐◓◑◒")
ASCII = Glyphs("+", "+", "+", "+", "=", "|", "+", "+", "#", "=", "-", "~", "*", ">", "|/-\\")


def glyphs(caps: Caps) -> Glyphs:
    return UNICODE if caps.unicode else ASCII


def pad(text: str, width: int) -> str:
    """``text`` padded or cut to exactly ``width`` visible columns."""
    gap = width - p.visible_len(text)
    if gap >= 0:
        return text + " " * gap
    return p.strip(text)[:width]  # styled text that overflows loses its colour, never words


def center(text: str, width: int) -> str:
    gap = max(0, width - p.visible_len(text))
    return " " * (gap // 2) + text + " " * (gap - gap // 2)


class Window:
    """A Win98-ish dialog: gradient title bar, framed black body."""

    def __init__(self, caps: Caps, title: str, width: int) -> None:
        self.caps = caps
        self.g = glyphs(caps)
        self.title = title
        self.inner = width - 2

    def _edge(self, text: str) -> str:
        return p.paint(text, p.SILVER, self.caps.color)

    def titlebar(self) -> str:
        buttons = "[_][X]"
        text = f" {self.title}"
        bar = text + " " * max(1, self.inner + 2 - len(text) - len(buttons)) + buttons
        bar = bar[: self.inner + 2]
        if self.caps.plain:
            return bar
        out = []
        for i, ch in enumerate(bar):
            bg = p.lerp(p.NAVY, p.TEAL, i / max(1, len(bar)))
            out.append(p.code(bg, self.caps.color, background=True) + ch)
        return p.BOLD + p.code(p.WHITE, self.caps.color) + "".join(out) + p.RESET

    def top(self) -> str:
        g = self.g
        return self._edge(g.tl + g.h * self.inner + g.tr)

    def bottom(self) -> str:
        g = self.g
        return self._edge(g.bl + g.h * self.inner + g.br)

    def rule(self) -> str:
        g = self.g
        return self._edge(g.ml + "─" * self.inner + g.mr) if self.caps.unicode else self.top()

    def row(self, text: str = "", *, centered: bool = False) -> str:
        body = center(text, self.inner) if centered else pad(text, self.inner)
        return self._edge(self.g.v) + pad(body, self.inner) + self._edge(self.g.v)


def button(caps: Caps, label: str, *, pressed: bool = False) -> str:
    text = f"[ {label} ]"
    if caps.plain:
        return text
    face = p.AMBER if pressed else p.SILVER
    return p.paint(text, p.INK, caps.color, bold=True, bg=face)
