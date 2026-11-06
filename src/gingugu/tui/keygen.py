"""The keygen: what `init` and `harness` print, as a show.

The command's own result lines are the content and are never reworded: each
gets a tag ([ OK ], [SKIP], [ >> ], [WARN]) and a colour, section headers
become LEVELs, and the score, the achievement and the finale are counted from
those same lines, so the fun is always true. Paths are printed whole, outside
the window, because a frame that cut a path would be lying about it.
"""

from __future__ import annotations

import hashlib
import random

from . import art, fx
from . import paint as p
from .caps import Caps

_OK = ("write", "overwrite", "wired", "updated", "appended", "refreshed", "added", "removed")
_OK += ("deleted", "created", "backed", "retired", "migrated", "pruned", "moved", "restored")
_SKIP = ("skip", "kept", "no change", "nothing", "none present", "left unchanged")
SHOW_SECONDS = 2.2  # how long the window animates before it settles
TAGS = {"ok": "[ OK ]", "preview": "[ >> ]", "skip": "[SKIP]", "warn": "[WARN]", "info": "      "}
_TAG_RGB = {"ok": p.GREEN, "preview": p.CYAN, "skip": p.AMBER, "warn": p.RED, "info": p.GREY}


def classify(line: str) -> str:
    s = line.strip()
    low = s.lower()
    if not s:
        return "blank"
    if s.endswith(":") and not s.startswith(("-", "/")):
        return "header"
    if low.startswith(("warn", "error")) or "warning:" in low:
        return "warn"
    if low.startswith("would"):
        return "preview"
    if low.startswith(_SKIP) or "already" in low:
        return "skip"
    if low.startswith(_OK):
        return "ok"
    return "info"


def score(results: list[str]) -> dict[str, int]:
    counts = {"ok": 0, "preview": 0, "skip": 0, "warn": 0}
    for line in results:
        kind = classify(line)
        if kind in counts:
            counts[kind] += 1
    counts["xp"] = 10 * counts["ok"] + 2 * counts["preview"] + counts["skip"]
    return counts


def achievement(counts: dict[str, int], dry_run: bool) -> tuple[str, str]:
    if counts["warn"]:
        return "SURVIVOR", f"{counts['warn']} warning(s) - read them above"
    if dry_run:
        return "WINDOW SHOPPER", f"{counts['preview']} change(s) previewed, nothing written"
    if counts["ok"] >= 10:
        return f"C0MB0 x{counts['ok']}", f"{counts['ok']} changes written in one run"
    if counts["ok"]:
        return "J4CK3D 1N", f"{counts['ok']} change(s) written"
    return "ALREADY 1337", "everything was already current"


def finale(counts: dict[str, int], dry_run: bool) -> str:
    if counts["warn"]:
        return "*** CR4CK3D W1TH W4RN1NGS *** read the [WARN] lines"
    if dry_run:
        return "*** D3M0 M0D3 *** N0 BYT3Z W3R3 WR1TT3N"
    return "*** CR4CK C0MPL3T3 *** SYST3M 4RM3D. w3lc0m3 t0 th3 c0r3. <<<"


def serial(subject: str, version: str) -> str:
    """A fake serial, stable per repo and version: decoration, not a secret."""
    digest = hashlib.sha256(f"gingugu:{subject}:{version}".encode()).hexdigest().upper()
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


def plain_lines(results: list[str], *, dry_run: bool, command: str, version: str) -> list[str]:
    """The whole report in ASCII: every word of the styled one, no escapes."""
    counts = score(results)
    boot = ">>> DRY RUN - N0 BYT3Z W1LL B3 WR1TT3N" if dry_run else ">>> J4CK1NG 1NT0 TH3 R3P0..."
    out = [f"G I N G U G U  ::  {command}  ::  v{version}", boot, ""]
    for line in results:
        kind, s = classify(line), line.strip()
        if kind == "blank":
            out.append("")
        elif kind == "header":
            out.append(">>> " + s[:-1].upper())
        else:
            out.append(f"  {TAGS[kind]} {s}")
    title, desc = achievement(counts, dry_run)
    out += [
        "",
        f">>> SCORE: {counts['ok']} ok, {counts['skip']} skipped, {counts['preview']} previewed, "
        f"{counts['warn']} warnings, +{counts['xp']} XP",
        f">>> ACHIEVEMENT UNLOCKED: {title} - {desc}",
        f">>> {finale(counts, dry_run)}",
    ]
    return out


def body_lines(caps: Caps, results: list[str]) -> list[str]:
    g = art.glyphs(caps)
    level = 0
    out = []
    for line in results:
        kind, s = classify(line), line.strip()
        if kind == "blank":
            out.append("")
        elif kind == "header":
            level += 1
            head = f"{g.arrow}{g.arrow} LEVEL {level} :: {s[:-1].upper()}"
            out.append(p.gradient(head, fx.NEON, caps.color, phase=level * 0.2, bold=True))
        else:
            tag = p.paint(TAGS[kind], _TAG_RGB[kind], caps.color, bold=True)
            text = s if kind in ("ok", "warn") else p.paint(s, _TAG_RGB[kind], caps.color)
            out.append(f"  {tag} {text}")
    return out


def window(caps: Caps, t: float, *, command: str, subject: str, version: str) -> list[str]:
    """The keygen dialog at time ``t``: rasterbars, logo, scroller, a serial being cracked."""
    width = min(caps.width - 1, 64)
    win = art.Window(caps, f"GiNGUGU v{version} :: {command.upper()} KEYG3N // CR0W", width)
    inner = win.inner
    cracked = t >= 1.4
    real = serial(subject, version)
    rng = random.Random(int(t * 20))
    shown = (
        real
        if cracked
        else "".join(c if c == "-" else rng.choice("0123456789ABCDEF") for c in real)
    )
    serial_txt = p.paint(shown, p.GREEN if cracked else p.MAGENTA, caps.color, bold=True)
    scroll = (
        "GINGUGU - one sqlite file - no cloud - no telemetry - never forgets ... "
        "greetz to crow, beepboop, tyrone and every agent who remembers ... "
    )
    rows = [win.titlebar(), win.top()]
    rows += [win.row(r) for r in fx.rasterbars(caps, t, inner, lines=1)]
    rows += [win.row(r) for r in fx.logo(caps, t, inner)]
    if t < SHOW_SECONDS:
        rows += [win.row(r) for r in fx.scroller(caps, scroll, t + 2, inner, lines=2)]
    else:  # the frame that stays on screen: a still tagline, not half a scroll
        tag = "0N3 SQL1T3 F1L3 // N0 CL0UD // N3V3R F0RG3TS"
        rows.append(win.row(p.gradient(tag, fx.NEON, caps.color, bold=True), centered=True))
        rows.append(win.row(p.paint("greetz: crow - beepboop - tyrone", p.GREY, caps.color),
                            centered=True))  # fmt: skip
    rows += [win.rule(), win.row(f"  Name   : {subject}"), win.row("  Serial : " + serial_txt)]
    buttons = "  ".join(
        [art.button(caps, "GENERATE", pressed=cracked), art.button(caps, "ABOUT"),
         art.button(caps, "EXIT")]
    )  # fmt: skip
    rows += [win.row(buttons, centered=True), win.bottom()]
    return rows


def footer(caps: Caps, t: float, counts: dict[str, int], dry_run: bool) -> list[str]:
    g = art.glyphs(caps)
    title, desc = achievement(counts, dry_run)
    flash = p.hue(t * 1.7) if t < 0.9 else p.YELLOW
    stats = (
        f"SCORE  ok {counts['ok']}  skip {counts['skip']}  preview {counts['preview']}  "
        f"warn {counts['warn']}  XP +{counts['xp']}"
    )
    badge = f"{g.star} ACHIEVEMENT UNLOCKED {g.star} {title}"
    return [
        "",
        p.paint(stats, p.CYAN, caps.color, bold=True),
        p.paint(badge, flash, caps.color, bold=True) + " - " + p.paint(desc, p.GREY, caps.color),
        p.gradient(finale(counts, dry_run), fx.NEON, caps.color, phase=t * 0.6, bold=True),
    ]
