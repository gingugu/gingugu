"""The keygen report and effects: true to the result lines, at every level."""

from __future__ import annotations

import io
import re

import pytest

from gingugu import tui
from gingugu.tui import art, fx, keygen
from gingugu.tui import paint as p
from gingugu.tui.caps import C16, C256, PLAIN, TRUECOLOR, Caps
from gingugu.tui.stage import HIDE, SHOW, Stage

RESULTS = [
    "Claude Code bootstrap:",
    "  write     /tmp/r/.claude/hooks/session_start.py",
    "  skip      /tmp/r/CLAUDE.md  (exists; use --force)",
    "  would write /tmp/r/AGENTS.md",
    "",
    "Claude Code trust:",
    "  warn   this folder is not trusted yet",
]
ESC = re.compile(r"\033\[")


def _caps(level: int, *, unicode: bool = True, animate: bool = False, width: int = 80) -> Caps:
    return Caps(color=level, unicode=unicode, width=width, height=60, animate=animate)


def _report(caps: Caps, results=RESULTS, dry_run=False) -> str:
    out = io.StringIO()
    tui.report(results, dry_run=dry_run, command="init", subject="r", stream=out, caps=caps)
    return out.getvalue()


def test_classify():
    kinds = [keygen.classify(line) for line in RESULTS]
    assert kinds == ["header", "ok", "skip", "preview", "blank", "header", "warn"]
    assert keygen.classify("  settings.json already wired") == "skip"
    assert keygen.classify("  WARNING: bad json") == "warn"
    assert keygen.classify("  restart Claude Code") == "info"


def test_score_and_achievement_come_from_the_lines():
    counts = keygen.score(RESULTS)
    assert (counts["ok"], counts["skip"], counts["preview"], counts["warn"]) == (1, 1, 1, 1)
    assert keygen.achievement(counts, False)[0] == "SURVIVOR"
    clean = keygen.score(["  write a", "  write b"])
    assert keygen.achievement(clean, False)[0] == "J4CK3D 1N"
    assert keygen.achievement(clean, True)[0] == "WINDOW SHOPPER"
    assert keygen.achievement(keygen.score(["  skip a"]), False)[0] == "ALREADY 1337"
    many = keygen.score([f"  write {i}" for i in range(12)])
    assert keygen.achievement(many, False)[0] == "C0MB0 x12"


def test_plain_report_is_ascii_and_keeps_every_line():
    out = _report(_caps(PLAIN, unicode=False))
    assert not ESC.search(out)
    assert out.isascii()
    for line in RESULTS:
        if line.strip() and not line.strip().endswith(":"):
            assert line.strip() in out
    assert "G I N G U G U  ::  init" in out
    assert ">>> CLAUDE CODE BOOTSTRAP" in out


@pytest.mark.parametrize("level", [C16, C256, TRUECOLOR])
@pytest.mark.parametrize("unicode", [True, False])
def test_styled_report_keeps_every_word(level, unicode):
    out = _report(_caps(level, unicode=unicode))
    assert ESC.search(out)
    text = p.strip(out)
    for line in RESULTS:
        if line.strip() and not line.strip().endswith(":"):
            assert line.strip() in text  # paths whole, never cut by a frame
    assert "ACHIEVEMENT UNLOCKED" in text
    if not unicode:
        assert "█" not in text and "║" not in text and "▀" not in text


def test_levels_emit_their_own_escape_families():
    assert "38;2;" in _report(_caps(TRUECOLOR))
    out256 = _report(_caps(C256))
    assert "38;5;" in out256 and "38;2;" not in out256
    out16 = _report(_caps(C16))
    assert "38;5;" not in out16 and "38;2;" not in out16
    assert re.search(r"\033\[(3[0-7]|9[0-7])m", out16)


def test_window_rows_are_one_width():
    for caps in (_caps(TRUECOLOR), _caps(C16, unicode=False), _caps(TRUECOLOR, width=50)):
        for t in (0.0, 0.7, 3.0):
            rows = keygen.window(caps, t, command="init", subject="r", version="1.0")
            widths = {p.visible_len(r) for r in rows}
            assert len(widths) == 1, widths
            assert widths.pop() <= caps.width - 1


def test_serial_is_stable_and_settles():
    assert keygen.serial("r", "1.0") == keygen.serial("r", "1.0")
    assert re.fullmatch(r"([0-9A-F]{4}-){3}[0-9A-F]{4}", keygen.serial("r", "1.0"))
    final = keygen.window(_caps(PLAIN), 3.0, command="init", subject="r", version="1.0")
    assert any(keygen.serial("r", "1.0") in row for row in final)


def test_quantizers():
    assert p.code((255, 85, 85), C16) == "\033[91m"
    assert p.code((200, 0, 0), C16) == "\033[31m"
    assert p.code((0, 0, 0), C16) == "\033[30m"
    assert p.code((255, 255, 255), C256) == "\033[38;5;231m"
    assert p.code((128, 128, 128), C256) == "\033[38;5;244m"
    assert p.code((1, 2, 3), TRUECOLOR, background=True) == "\033[48;2;1;2;3m"
    assert p.code((1, 2, 3), PLAIN) == ""
    assert p.gradient("abc", fx.NEON, PLAIN) == "abc"


@pytest.mark.parametrize("effect", [fx.plasma, fx.rasterbars])
def test_pixel_effects_fill_the_width(effect):
    for caps in (_caps(TRUECOLOR), _caps(C16, unicode=False)):
        rows = effect(caps, 1.0, 30, lines=2)
        assert len(rows) == 2 and all(p.visible_len(r) == 30 for r in rows)


def test_scroller_and_progress():
    rows = fx.scroller(_caps(PLAIN), "HELLO", 0.5, 20, lines=3)
    assert len(rows) == 3 and all(len(r) == 20 for r in rows)
    assert fx.progress(_caps(PLAIN), "X", 0.5, 0, 30).count("#") > 0
    assert "100%" in fx.progress(_caps(TRUECOLOR), "X", 2.0, 0, 40)


def test_narrow_terminal_logo_falls_back_to_text():
    rows = fx.logo(_caps(PLAIN), 0, art.LOGO_WIDTH - 1)
    assert any("G I N G U G U" in r for r in rows)


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_stage_hides_and_always_restores_the_cursor():
    out = _TTY()
    caps = Caps(color=TRUECOLOR, unicode=True, width=80, height=40, animate=True)
    with Stage(caps, out) as stage:
        stage.play(lambda t: ["a", "b"], 0.05)
    text = out.getvalue()
    assert text.startswith(HIDE) and text.endswith(SHOW)
    assert "\033[2F" in text  # redrew in place


def test_ctrl_c_skips_the_show_but_not_the_output():
    out = _TTY()
    caps = Caps(color=TRUECOLOR, unicode=True, width=80, height=40, animate=True)

    def boom(t: float) -> list[str]:
        if 0 < t < 0.5:
            raise KeyboardInterrupt
        return ["frame"]

    with Stage(caps, out) as stage:
        stage.play(boom, 1.0)
        stage.reveal(["line one", "line two"])
    assert stage.interrupted and stage.skipped
    text = out.getvalue()
    assert "line one" in text and "line two" in text and text.endswith(SHOW)


def test_frame_taller_than_screen_is_shown_once_not_animated():
    out = _TTY()
    caps = Caps(color=TRUECOLOR, unicode=True, width=80, height=3, animate=True)
    with Stage(caps, out) as stage:
        stage.play(lambda t: ["1", "2", "3", "4"], 5.0)
    assert "\033[4F" not in out.getvalue()


def test_banner_and_version_are_plain_off_a_tty():
    assert tui.banner(io.StringIO()) == ""
    assert tui.version_line("1.2.3", io.StringIO()) == "gingugu 1.2.3"


def test_run_passes_the_exit_code_through_and_is_silent_when_plain():
    out = io.StringIO()
    assert tui.run("remote", lambda: 3, stream=out) == 3
    assert out.getvalue() == ""
