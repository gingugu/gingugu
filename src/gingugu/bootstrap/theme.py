"""90s h@x0rZ terminal theme for `gingugu init` output.

Green-on-black boot-sequence vibe: ASCII banner, `[ OK ]` boot log, leet
flavor. Cosmetic only — degrades to clean monochrome when stdout is not a TTY
or ``NO_COLOR`` is set, and never leetspeaks file paths (those stay accurate).
"""

from __future__ import annotations

import os
import sys

_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_WIDTH = 58
_BANNER_LINES = [
    "",
    "░▒▓█  G I N G U G U  █▓▒░",
    "M 3 M 0 R Y   R 1 G   ·   1 N 1 T",
    "",
]


def _color_on() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(text: str, code: str, *, bold: bool = False) -> str:
    if not _color_on():
        return text
    prefix = (_BOLD if bold else "") + code
    return f"{prefix}{text}{_RESET}"


def _banner() -> str:
    top = "╔" + "═" * _WIDTH + "╗"
    bot = "╚" + "═" * _WIDTH + "╝"
    mid = ["║" + line.center(_WIDTH) + "║" for line in _BANNER_LINES]
    return "\n".join(_c(row, _GREEN, bold=True) for row in [top, *mid, bot])


def _style_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    low = s.lower()
    if s.endswith(":"):  # section header, e.g. "Claude Code bootstrap:"
        return _c(">>> " + s[:-1].upper(), _GREEN, bold=True)
    if low.startswith("would"):  # dry-run preview
        return _c("  [ >> ] ", _CYAN) + _c(s, _CYAN)
    if low.startswith(("write", "overwrite", "wired", "updated")):
        return _c("  [ OK ] ", _GREEN) + _c(s, _GREEN)
    if low.startswith("skip") or "already" in low:
        return _c("  [SKIP] ", _YELLOW) + _c(s, _DIM)
    return _c("  " + s, _CYAN)  # info / next-steps


def render(results: list[str], *, dry_run: bool) -> str:
    boot = ">>> DRY RUN - N0 BYT3Z W1LL B3 WR1TT3N" if dry_run else ">>> J4CK1NG 1NT0 TH3 R3P0..."
    out = [_banner(), _c(boot, _GREEN, bold=True), ""]
    out.extend(_style_line(line) for line in results)
    out.append("")
    out.append(_c(">>> SYST3M 4RM3D. w3lc0m3 t0 th3 c0r3. <<<", _GREEN, bold=True))
    return "\n".join(out)
