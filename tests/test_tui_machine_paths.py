"""Machine paths never wear the costume.

Hooks are read by Claude Code, the stdio server and the proxy speak MCP on
stdout, and ``serve``/``board watch``/``xray``/``dream`` feed other programs.
Their bytes must not change on a fancy terminal: proven here on a real pty
with a truecolor environment, against the same run with the TUI forced off.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "gingugu"
# The only modules allowed to import the TUI, and why.
ALLOWED = {
    "server.py",  # help / version / party / _show for human-facing commands only
    "bootstrap/__init__.py",  # gingugu init
    "bootstrap/harness.py",  # gingugu harness
}

_RUNNER = """
import sys
sys.argv = ["gingugu", "hook", sys.argv[1]]
from gingugu.server import main
try:
    main()
except SystemExit:
    pass
finally:
    sys.stderr.write("\\nTUI_LOADED=%s\\n" % ("gingugu.tui" in sys.modules))
"""


def _imports_tui(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {a.name for a in node.names}
            if "tui" in mod.split(".") or (mod in ("", "..") and "tui" in names):
                return True
            if mod == "gingugu" and "tui" in names:
                return True
        if isinstance(node, ast.Import) and any("gingugu.tui" in a.name for a in node.names):
            return True
    return False


def test_only_human_facing_modules_import_the_tui():
    importers = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "tui" not in path.relative_to(SRC).parts and _imports_tui(path)
    }
    assert importers == ALLOWED


def _run_on_pty(hook: str, env: dict[str, str], payload: bytes) -> tuple[bytes, str]:
    import pty
    import termios
    import threading

    master, slave = pty.openpty()
    # Raw output: with OPOST the kernel's \n -> \r\n can emit \r\r\n under load,
    # which would make two identical runs compare unequal.
    attrs = termios.tcgetattr(slave)
    attrs[1] &= ~termios.OPOST
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    chunks: list[bytes] = []

    def drain() -> None:  # a pty buffer is small; read while the hook writes
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
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _RUNNER, hook],
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=subprocess.PIPE,
            env=env,
        )
        os.close(slave)
        _, err = proc.communicate(payload, timeout=120)
        reader.join(10)
    finally:
        os.close(master)
    return b"".join(chunks), err.decode(errors="replace")


def _payload(hook: str) -> bytes:
    """A payload that makes the hook speak, so the comparison is not of two silences."""
    import json

    root = SRC.parents[1]
    if hook == "read":
        tool = {"tool_name": "Read", "tool_input": {"file_path": str(SRC / "server.py")}}
    elif hook == "tool":  # a minion reaching for the brain's database: the fence answers
        tool = {"tool_name": "Bash", "tool_input": {"command": "sqlite3 memories.db"}}
        tool["agent_id"] = "minion-1"
    else:
        tool = {"prompt": "how does the board inbox work", "tool_name": "Read"}
    return json.dumps({**tool, "cwd": str(root), "session_id": "pty-probe"}).encode()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
@pytest.mark.parametrize("hook", ["prompt", "tool", "subagent", "read", "bash", "edit"])
def test_hooks_are_byte_identical_on_a_fancy_terminal(hook, tmp_path):
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "MEMORY_DB_PATH": str(tmp_path / "m.db"),
        "MEMORY_REMOTE_URL": "off",
        "GINGUGU_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": str(SRC.parent),
        "XDG_DATA_HOME": str(tmp_path / "xdg"),
    }
    (tmp_path / "home").mkdir()
    # Each run gets its own data dir: hooks keep per-session state ("first read gets a map").
    fancy = {**base, "COLORTERM": "truecolor", "TERM": "xterm-256color", "LANG": "en_US.UTF-8"}
    fancy["MEMORY_DB_PATH"] = str(tmp_path / "fancy" / "m.db")
    plain = {**base, "GINGUGU_PLAIN": "1", "TERM": "dumb"}
    plain["MEMORY_DB_PATH"] = str(tmp_path / "plain" / "m.db")
    payload = _payload(hook)
    out_fancy, err_fancy = _run_on_pty(hook, fancy, payload)
    out_plain, _ = _run_on_pty(hook, plain, payload)
    assert out_fancy == out_plain
    if hook in ("read", "tool"):
        assert out_fancy.strip(), "the probe payload should make this hook print"
    assert b"\033[" not in out_fancy
    assert "TUI_LOADED=False" in err_fancy
