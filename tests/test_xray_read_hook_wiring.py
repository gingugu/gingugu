"""X14 wired in: ``gingugu hook read``, its PostToolUse entry and its doorway.

The map itself is specified in ``test_xray_read_hook``; this is how it reaches
Claude Code - as ``additionalContext`` next to the tool result, never a block,
never a crash, and never a process spawned for a tool it does not watch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from gingugu import post_tool_hook
from gingugu.bootstrap import main as init_main
from gingugu.bootstrap.settings import merge_settings
from gingugu.xray import read_hook
from tests.test_xray_edit_hook import BASIC, _repo
from tests.test_xray_read_hook import _read

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake gingugu is a shell script")


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "db" / "memories.db"))
    monkeypatch.delenv("MEMORY_XRAY_MAP", raising=False)
    return tmp_path


# --- gingugu hook read ----------------------------------------------------------


def test_the_hook_adds_context_next_to_the_tool_result(repo, hook_env, capsys):
    assert post_tool_hook.run(_read(repo, "pkg/core.py")) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    assert "X-RAY MAP" in spec["additionalContext"]
    assert "pkg/a.py" in spec["additionalContext"]
    assert "decision" not in out  # informs, never blocks


def test_nothing_to_say_prints_nothing(repo, hook_env, capsys):
    post_tool_hook.run(_read(repo, "pkg/core.py"))
    capsys.readouterr()
    post_tool_hook.run(_read(repo, "pkg/core.py"))
    assert capsys.readouterr().out == ""


def test_the_switch_turns_it_off(repo, hook_env, capsys, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_MAP", "off")
    post_tool_hook.run(_read(repo, "pkg/core.py"))
    assert capsys.readouterr().out == ""


def test_the_edit_tripwire_switch_does_not_turn_it_off(repo, hook_env, capsys, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TRIPWIRE", "off")
    post_tool_hook.run(_read(repo, "pkg/core.py"))
    assert "X-RAY MAP" in capsys.readouterr().out


def test_a_crashing_map_never_breaks_the_hook(repo, hook_env, capsys, monkeypatch):
    def boom(payload, state_dir):
        raise RuntimeError("bug")

    monkeypatch.setattr(read_hook, "check", boom)
    assert post_tool_hook.run(_read(repo, "pkg/core.py")) == 0
    assert capsys.readouterr().out == ""


def test_the_state_dir_is_beside_the_db(repo, hook_env, capsys):
    from pathlib import Path

    post_tool_hook.run(_read(repo, "pkg/core.py"))
    state_dir = Path(os.environ["MEMORY_DB_PATH"]).parent / "hook-sessions"
    assert list(state_dir.glob("xray-graph-*.json"))


def test_main_survives_garbage_on_stdin(monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert post_tool_hook.main() == 0
    assert capsys.readouterr().out == ""


def test_cli_dispatches_hook_read():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gingugu.server import main; "
            "sys.argv=['gingugu','hook','read']; main()",
        ],
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


# --- gingugu init -----------------------------------------------------------------


def test_init_wires_posttooluse_for_reads_and_searches_only():
    settings, added, _ = merge_settings({})
    assert "PostToolUse" in added
    (group,) = settings["hooks"]["PostToolUse"]
    assert set(group["matcher"].replace(" ", "").split("|")) == {"Read", "Grep", "Glob"}
    assert "post_tool_xray.py" in group["hooks"][0]["command"]


def test_init_installs_the_doorway(tmp_path):
    init_main(["--path", str(tmp_path)])
    script = tmp_path / ".claude" / "hooks" / "post_tool_xray.py"
    assert script.exists()
    text = script.read_text()
    assert "gingugu-init:managed-file" in text
    assert '"read"' in text


@pytest.fixture
def doorway(tmp_path):
    init_main(["--path", str(tmp_path)])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fake = bin_dir / "gingugu"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncat > /dev/null\necho \'{{"ok": 1}}\'\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return tmp_path / ".claude" / "hooks" / "post_tool_xray.py", log, env


def _door(script, payload: dict, env) -> str:
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    return proc.stdout


@posix_only
def test_the_doorway_passes_a_read(doorway):
    script, log, env = doorway
    assert _door(script, {"tool_name": "Read", "tool_input": {}}, env).strip() == '{"ok": 1}'
    assert log.read_text().split() == ["hook", "read"]


@posix_only
def test_the_doorway_drops_other_tools_before_loading_gingugu(doorway):
    script, log, env = doorway
    assert _door(script, {"tool_name": "Bash", "tool_input": {}}, env) == ""
    assert not log.exists()


@posix_only
def test_the_doorway_never_fails_on_garbage(doorway):
    script, log, env = doorway
    proc = subprocess.run(
        [sys.executable, str(script)], input="nope", capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0 and proc.stdout == ""
