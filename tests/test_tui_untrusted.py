"""Untrusted names (a repo dir, a path, a blocker name) never reach the terminal raw.

The only escape sequences in the show are the ones ``tui`` writes itself; a
repo directory named with newlines, OSC title sets, a screen clear or bidi
overrides is shown as harmless text, on one line, in every mode.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from gingugu import showable, tui
from gingugu.tui import keygen
from gingugu.tui.caps import PLAIN, TRUECOLOR, Caps

EVIL = "re\npo\r\x1b]0;pwn\x07\x1b[2J‮mid\x9b31m​end\x7f"
_OWN_CSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
_UNSAFE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f​-‏ -‮⁦-⁩﻿]")
SRC = Path(__file__).resolve().parents[1] / "src"


def _results(name: str) -> list[str]:
    return [
        "Claude Code bootstrap:",
        f"  write     /tmp/{name}/.claude/hooks/session_start.py",
        f"  skip    hooks/utils: {name} mentions it and may still need it",
    ]


def _report(caps: Caps, name: str) -> str:
    out = io.StringIO()
    tui.report(_results(name), dry_run=False, command="init", subject=name, stream=out, caps=caps)
    return out.getvalue()


def _assert_clean(out: str) -> None:
    assert "\x1b]" not in out and "\x1b[2J" not in out and "\x07" not in out
    rest = _OWN_CSI.sub("", out)
    assert "\x1b" not in rest
    assert not _UNSAFE.search(rest), repr(_UNSAFE.search(rest))


def test_clean_strips_controls_escapes_and_bidi():
    assert showable.clean("a\x1b]0;pwn\x07b") == "ab"
    assert showable.clean("a\x1b[2Jb") == "ab"
    assert showable.clean("a\nb\rc\x00d\x7fe\x9bf‮g​h") == "a b c d e f g h"
    assert showable.clean("plain/path name") == "plain/path name"


@pytest.mark.parametrize(
    "caps",
    [
        Caps(color=PLAIN, unicode=False, width=80, height=60),
        Caps(color=TRUECOLOR, unicode=True, width=80, height=60, animate=False),
    ],
)
def test_report_shows_untrusted_names_harmlessly(caps):
    out = _report(caps, EVIL)
    _assert_clean(out)
    assert out.count("\n") == _report(caps, "repo").count("\n")  # one line per name


def test_serial_comes_from_the_cleaned_name():
    caps = Caps(color=TRUECOLOR, unicode=True, width=80, height=60, animate=False)
    out = _OWN_CSI.sub("", _report(caps, EVIL))
    assert keygen.serial(showable.clean(EVIL), tui._version()) in out


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_animated_show_on_a_pty_is_clean(tmp_path):
    import pty
    import termios

    code = (
        "import sys; from gingugu import tui; name = sys.argv[1]\n"
        "tui.report([f'  write /tmp/{name}/x.py', f'  skip hooks/utils: {name} mentions it'],"
        " dry_run=False, command='init', subject=name)\n"
    )
    master, slave = pty.openpty()
    # Raw output: with OPOST the kernel turns \n into \r\n, and when the queue
    # backs up under load it can emit \r\r\n. Off, every \r seen is the show's own.
    attrs = termios.tcgetattr(slave)
    attrs[1] &= ~termios.OPOST
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    chunks: list[bytes] = []

    def drain() -> None:
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            if not chunk:
                return
            chunks.append(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    env = {
        "PATH": "/nonexistent",
        "HOME": str(tmp_path),
        "GINGUGU_QUIET": "1",
        "GINGUGU_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": str(SRC),
        "COLORTERM": "truecolor",
        "TERM": "xterm-256color",
        "COLUMNS": "90",
        "LINES": "40",
    }
    proc = subprocess.Popen([sys.executable, "-c", code, EVIL], stdin=slave, stdout=slave, env=env)
    os.close(slave)
    assert proc.wait(timeout=60) == 0
    reader.join(5)
    os.close(master)
    out = b"".join(chunks).decode("utf-8", "replace")
    _assert_clean(out)
    assert "mid" in out and "end" in out  # the readable parts survive
