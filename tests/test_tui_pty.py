"""The full show on a real pty: finishes fast, keeps every word, restores the terminal."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
_SHOW = """
from gingugu import tui
tui.report(
    ["Claude Code bootstrap:", "  write     /tmp/x/.claude/hooks/session_start.py",
     "  skip      /tmp/x/CLAUDE.md  (exists)"],
    dry_run=False, command="init", subject="x",
)
"""


def _pty_run(code: str, env: dict[str, str], stdin_bytes: bytes = b"") -> tuple[bytes, float, int]:
    import pty

    master, slave = pty.openpty()
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
    start = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=slave, stdout=slave, env=env)
    os.close(slave)
    if stdin_bytes:
        time.sleep(0.6)
        os.write(master, stdin_bytes)
    rc = proc.wait(timeout=60)
    elapsed = time.monotonic() - start
    reader.join(5)
    os.close(master)
    return b"".join(chunks), elapsed, rc


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": "/nonexistent",  # no paplay/aplay on PATH
        "HOME": str(tmp_path),
        "GINGUGU_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": str(SRC),
        "COLORTERM": "truecolor",
        "TERM": "xterm-256color",
        "COLUMNS": "90",
        "LINES": "40",
        **extra,
    }


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_full_show_runs_and_restores(tmp_path):
    # Muted: macOS plays through the fixed /usr/bin/afplay whatever PATH says, and a
    # test run must not make noise. Caching is covered in test_tui_audio/hardening.
    out, elapsed, rc = _pty_run(_SHOW, _env(tmp_path, GINGUGU_QUIET="1"))
    assert rc == 0
    assert elapsed < 15
    assert out.startswith(b"\033[?25l") and out.rstrip().endswith(b"\033[?25h")
    text = re.sub(rb"\033\[[0-9;?]*[A-Za-z]", b"", out).decode()
    assert "/tmp/x/.claude/hooks/session_start.py" in text
    assert "ACHIEVEMENT UNLOCKED" in text


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_any_key_skips_to_the_end(tmp_path):
    full, full_t, _ = _pty_run(_SHOW, _env(tmp_path, GINGUGU_QUIET="1"))
    _, skipped_t, rc = _pty_run(_SHOW, _env(tmp_path, GINGUGU_QUIET="1"), stdin_bytes=b" ")
    assert rc == 0
    assert skipped_t < full_t
    assert b"session_start.py" in full


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_party_plays_and_ends(tmp_path):
    code = "from gingugu.tui import party; raise SystemExit(party.main(['1.5']))"
    out, elapsed, rc = _pty_run(code, _env(tmp_path, GINGUGU_QUIET="1"))
    assert rc == 0 and elapsed < 15
    assert b"\033[?25h" in out  # cursor back
    assert b"J4CK3D 0UT" in re.sub(rb"\033\[[0-9;?]*[A-Za-z]", b"", out)
