"""The capability ladder: what each terminal gets, and the plain floor."""

from __future__ import annotations

import io

import pytest

from gingugu.tui import caps as c


class FakeTTY(io.StringIO):
    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding

    def isatty(self) -> bool:
        return True


def _detect(env: dict[str, str], *, platform: str = "linux", vt: bool | None = None, **kw):
    stream = kw.pop("stream", FakeTTY())
    return c.detect(stream, env, platform=platform, stdin=FakeTTY(), vt=vt)


@pytest.mark.parametrize(
    "env, level",
    [
        ({"COLORTERM": "truecolor", "TERM": "xterm-256color"}, c.TRUECOLOR),
        ({"COLORTERM": "24bit"}, c.TRUECOLOR),
        ({"TERM_PROGRAM": "iTerm.app", "TERM": "xterm-256color"}, c.TRUECOLOR),
        ({"TERM_PROGRAM": "vscode"}, c.TRUECOLOR),
        ({"TERM_PROGRAM": "Apple_Terminal", "TERM": "xterm-256color"}, c.C256),
        ({"TERM": "screen-256color"}, c.C256),
        ({"TERM": "xterm"}, c.C16),
        ({"TERM": "linux"}, c.C16),
        ({}, c.C16),
    ],
)
def test_colour_ladder(env, level):
    caps = _detect(env)
    assert caps.color == level
    assert caps.animate and caps.unicode and caps.keys


@pytest.mark.parametrize(
    "env",
    [
        {"NO_COLOR": "1", "COLORTERM": "truecolor"},
        {"TERM": "dumb", "COLORTERM": "truecolor"},
        {"GINGUGU_PLAIN": "1", "COLORTERM": "truecolor"},
        {"GINGUGU_COLORS": "plain", "COLORTERM": "truecolor"},
    ],
)
def test_plain_switches(env):
    caps = _detect(env)
    assert caps.plain and not caps.animate and not caps.unicode


def test_not_a_tty_is_plain_whatever_the_env_says():
    caps = c.detect(io.StringIO(), {"COLORTERM": "truecolor"}, platform="linux")
    assert caps.plain and not caps.animate


def test_empty_no_color_does_not_disable():  # no-color.org: only a non-empty value counts
    assert _detect({"NO_COLOR": "", "TERM": "xterm"}).color == c.C16


def test_plain_off_value_is_not_plain():
    assert not _detect({"GINGUGU_PLAIN": "0", "TERM": "xterm"}).plain


def test_ci_keeps_colour_but_never_animates():
    caps = _detect({"CI": "true", "COLORTERM": "truecolor"})
    assert caps.color == c.TRUECOLOR and not caps.animate


def test_no_anim_keeps_colour():
    caps = _detect({"GINGUGU_NO_ANIM": "1", "TERM": "xterm-256color"})
    assert caps.color == c.C256 and not caps.animate


@pytest.mark.parametrize("forced, level", [("16", c.C16), ("256", c.C256), ("truecolor", 3)])
def test_forced_level(forced, level):
    assert _detect({"GINGUGU_COLORS": forced}).color == level


def test_non_utf8_stream_gets_ascii_glyphs():
    caps = _detect({"TERM": "xterm"}, stream=FakeTTY("cp437"))
    assert caps.color == c.C16 and not caps.unicode


def test_ascii_override():
    assert not _detect({"TERM": "xterm", "GINGUGU_ASCII": "1"}).unicode


def test_windows_without_vt_is_plain():
    assert _detect({"COLORTERM": "truecolor"}, platform="win32", vt=False).plain


def test_windows_terminal_is_truecolor():
    assert _detect({"WT_SESSION": "x"}, platform="win32", vt=True).color == c.TRUECOLOR


def test_windows_conhost_with_vt_is_256():
    assert _detect({}, platform="win32", vt=True).color == c.C256


def test_windows_legacy_codepage_is_ascii():
    caps = _detect({}, platform="win32", vt=True, stream=FakeTTY("cp437"))
    assert not caps.unicode


def test_width_is_clamped(monkeypatch):
    monkeypatch.setenv("COLUMNS", "20")
    assert _detect({"TERM": "xterm"}).width == c.MIN_WIDTH
    monkeypatch.setenv("COLUMNS", "400")
    assert _detect({"TERM": "xterm"}).width == c.MAX_WIDTH
