"""X15 wired in: the snapshot in ``gingugu hook tool``, the diff in ``gingugu hook
bash``, its PostToolUse entry and its doorway.

The behaviour is specified in ``test_xray_bash_hook``; this is how it reaches
Claude Code. The snapshot rides the PreToolUse doorway that already passes every
Bash call and never denies; the report is ``additionalContext`` from a NEW
doorway matched to ``Bash`` (a changed matcher on an existing entry would never
reach an already-initialised repo, a new script does).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from gingugu import post_tool_hook, tool_hook
from gingugu.bootstrap import main as init_main
from gingugu.bootstrap.settings import merge_settings
from tests.test_xray_bash_hook import _bash
from tests.test_xray_edit_hook import BASIC, _repo

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake gingugu is a shell script")


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "db" / "memories.db"))
    monkeypatch.delenv("MEMORY_XRAY_TRIPWIRE", raising=False)
    monkeypatch.delenv("MEMORY_TRIPWIRES", raising=False)
    return tmp_path


def test_the_pretooluse_hook_snapshots_a_bash_call_and_never_denies(repo, hook_env, capsys):
    assert tool_hook.run(_bash(repo)) == 0
    assert capsys.readouterr().out == ""  # no deny, no message
    (repo / "pkg/core.py").write_text("X = 2\n")
    assert post_tool_hook.run(_bash(repo, event="PostToolUse")) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    assert "pkg/core.py" in spec["additionalContext"]
    assert "pkg/a.py" in spec["additionalContext"]
    assert "decision" not in out  # informs, never blocks


def test_nothing_changed_prints_nothing(repo, hook_env, capsys):
    tool_hook.run(_bash(repo))
    post_tool_hook.run(_bash(repo, event="PostToolUse"))
    assert capsys.readouterr().out == ""


def test_the_read_tools_still_get_the_map(repo, hook_env, capsys):
    from tests.test_xray_read_hook import _read

    post_tool_hook.run(_read(repo, "pkg/core.py"))
    assert "X-RAY MAP" in capsys.readouterr().out


def test_a_crashing_snapshot_never_breaks_the_tool_hook(repo, hook_env, capsys, monkeypatch):
    from gingugu.xray import bash_hook

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(bash_hook, "before", boom)
    assert tool_hook.run(_bash(repo)) == 0
    assert capsys.readouterr().out == ""


def test_cli_dispatches_hook_bash():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gingugu.server import main; "
            "sys.argv=['gingugu','hook','bash']; main()",
        ],
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


# --- gingugu init -----------------------------------------------------------------


def test_init_wires_a_posttooluse_entry_for_bash_beside_the_read_one():
    settings, added, _ = merge_settings({})
    groups = settings["hooks"]["PostToolUse"]
    by_script = {
        ("post_bash_xray.py" if "post_bash_xray.py" in g["hooks"][0]["command"] else "read"): g
        for g in groups
        if "post_edit_xray.py" not in g["hooks"][0]["command"]  # the edit note has its own test
    }
    assert by_script["post_bash_xray.py"]["matcher"] == "Bash"
    assert set(by_script["read"]["matcher"].split("|")) == {"Read", "Grep", "Glob"}


def test_init_adds_the_bash_entry_to_an_already_initialised_repo():
    old, _, _ = merge_settings({})
    old["hooks"]["PostToolUse"] = [
        g for g in old["hooks"]["PostToolUse"] if "post_bash_xray.py" not in json.dumps(g)
    ]
    settings, _, _ = merge_settings(old)
    assert "post_bash_xray.py" in json.dumps(settings["hooks"]["PostToolUse"])
    assert json.dumps(settings).count("post_tool_xray.py") == 1  # the read entry not doubled


def test_init_installs_the_doorway(tmp_path):
    init_main(["--path", str(tmp_path)])
    script = tmp_path / ".claude" / "hooks" / "post_bash_xray.py"
    assert script.exists()
    text = script.read_text()
    assert "gingugu-init:managed-file" in text
    assert '"bash"' in text


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
    return tmp_path / ".claude" / "hooks" / "post_bash_xray.py", log, env


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
def test_the_doorway_passes_a_bash_call(doorway):
    script, log, env = doorway
    assert _door(script, {"tool_name": "Bash", "tool_input": {}}, env).strip() == '{"ok": 1}'
    assert log.read_text().split() == ["hook", "bash"]


@posix_only
def test_the_doorway_drops_other_tools_before_loading_gingugu(doorway):
    script, log, env = doorway
    assert _door(script, {"tool_name": "Read", "tool_input": {}}, env) == ""
    assert not log.exists()


@posix_only
def test_the_doorway_never_fails_on_garbage(doorway):
    script, log, env = doorway
    proc = subprocess.run(
        [sys.executable, str(script)], input="nope", capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0 and proc.stdout == ""


@posix_only
def test_the_pretooluse_doorway_passes_a_bash_call_to_gingugu(tmp_path):
    """The snapshot lives behind pre_tool_tripwire.py: it must not drop Bash."""
    init_main(["--path", str(tmp_path)])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fake = bin_dir / "gingugu"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncat > /dev/null\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    script = tmp_path / ".claude" / "hooks" / "pre_tool_tripwire.py"
    _door(script, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, env)
    assert log.read_text().split() == ["hook", "tool"]
