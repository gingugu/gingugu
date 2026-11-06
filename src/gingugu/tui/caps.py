"""What this terminal can do, decided once per command.

The ladder is truecolor -> 256 -> 16 -> plain. Plain is the floor and the
default whenever there is doubt: not a TTY, ``NO_COLOR``, ``TERM=dumb``,
``GINGUGU_PLAIN``, or a Windows console that will not take VT escapes. Plain
output is ASCII and carries every word the styled output does, so a pipe, a
log or a screen reader loses nothing but the party.

Animation needs more again: a colour TTY, not CI, and no ``GINGUGU_NO_ANIM``.

Overrides, for demos and bug reports:
  GINGUGU_COLORS = plain | 16 | 256 | truecolor   (forces the colour level)
  GINGUGU_ASCII  = 1                              (box drawing and blocks off)
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

PLAIN, C16, C256, TRUECOLOR = 0, 1, 2, 3
_LEVELS = {"plain": PLAIN, "0": PLAIN, "16": C16, "256": C256, "truecolor": TRUECOLOR}
_LEVELS["24bit"] = TRUECOLOR
_TRUECOLOR_APPS = {"iTerm.app", "WezTerm", "vscode", "Hyper", "ghostty", "Tabby", "rio"}
MIN_WIDTH, MAX_WIDTH = 40, 100


@dataclass(frozen=True)
class Caps:
    """One command's terminal: colour level, glyph set, width, motion."""

    color: int = PLAIN
    unicode: bool = False
    width: int = 80
    height: int = 24
    animate: bool = False
    keys: bool = False  # stdin is a TTY, so a keypress can skip an animation

    @property
    def plain(self) -> bool:
        return self.color == PLAIN


PLAIN_CAPS = Caps()


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in ("0", "false", "no", "off", "")


def _isatty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return False


def _utf8(stream: object) -> bool:
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    return encoding in ("utf8", "utf_8")


def enable_windows_vt() -> bool:  # pragma: no cover - exercised on Windows only
    """Turn on VT escape processing for this console; False if it refuses."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _color_level(env: Mapping[str, str], platform: str, vt: bool) -> int:
    forced = env.get("GINGUGU_COLORS", "").strip().lower()
    if forced in _LEVELS:
        return _LEVELS[forced]
    if platform == "win32":
        if not vt:
            return PLAIN
        return TRUECOLOR if env.get("WT_SESSION") else C256
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return TRUECOLOR
    if env.get("TERM_PROGRAM") in _TRUECOLOR_APPS:
        return TRUECOLOR
    if "256color" in env.get("TERM", ""):
        return C256
    return C16


def detect(
    stream: TextIO | None = None,
    env: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    stdin: object | None = None,
    vt: bool | None = None,
) -> Caps:
    """The capabilities of ``stream`` (stdout) under ``env`` (os.environ)."""
    stream = sys.stdout if stream is None else stream
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    stdin = sys.stdin if stdin is None else stdin
    size = shutil.get_terminal_size((80, 24))
    width = max(MIN_WIDTH, min(MAX_WIDTH, size.columns))
    height = max(1, size.lines)
    if not _isatty(stream) or _truthy(env.get("GINGUGU_PLAIN")):
        return Caps(width=width, height=height)
    # NO_COLOR: any non-empty value disables colour (no-color.org).
    if env.get("NO_COLOR") or env.get("TERM") == "dumb":
        return Caps(width=width, height=height)
    if platform == "win32" and vt is None:
        vt = enable_windows_vt()
    level = _color_level(env, platform, bool(vt))
    if level == PLAIN:
        return Caps(width=width, height=height)
    unicode = _utf8(stream) and not _truthy(env.get("GINGUGU_ASCII"))
    motion = not _truthy(env.get("CI")) and not _truthy(env.get("GINGUGU_NO_ANIM"))
    return Caps(
        color=level,
        unicode=unicode,
        width=width,
        height=height,
        animate=motion,
        keys=_isatty(stdin),
    )
