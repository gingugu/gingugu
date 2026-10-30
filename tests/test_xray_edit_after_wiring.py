"""The post-edit note and the test ledger, wired in: ``gingugu hook edit``, its
PostToolUse entry and doorway, and the Bash PostToolUse path that records test runs.

Behaviour is specified in ``test_xray_edit_after`` and ``test_xray_ledger``.
Like X15, the note gets a NEW doorway (``post_edit_xray.py``, matched to
``Edit|Write|MultiEdit``): a changed matcher on the existing read entry would
never reach an already-initialised repo, a new script does.
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
from gingugu.xray import ledger
from tests.test_xray_edit_after import CALLS, _after
from tests.test_xray_edit_hook import _repo
from tests.test_xray_ledger import PYTEST_FAIL, _bash

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake gingugu is a shell script")


@pytest.fixture
def calls(tmp_path):
    return _repo(tmp_path, CALLS)


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "db" / "memories.db"))
    monkeypatch.delenv("MEMORY_XRAY_TRIPWIRE", raising=False)
    return tmp_path / "db" / "hook-sessions"


def test_an_edit_gets_its_note_as_additional_context(calls, hook_env, capsys):
    p = _after(calls, "pkg/core.py", old_string="x", new_string="    return 1")
    assert post_tool_hook.run(p) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    assert spec["additionalContext"].startswith("GINGUGU X-RAY after edit")
    assert "decision" not in out  # informs, never blocks


def test_a_test_run_lands_in_the_ledger_with_no_snapshot(calls, hook_env, capsys):
    assert post_tool_hook.run(_bash(calls, "uv run pytest -q", stdout=PYTEST_FAIL)) == 0
    assert "tests/test_svc.py" in ledger.path(hook_env).read_text()
    assert capsys.readouterr().out == ""


def test_a_crashing_note_never_breaks_the_hook(calls, hook_env, capsys, monkeypatch):
    from gingugu.xray import edit_note

    monkeypatch.setattr(edit_note, "check", lambda *a: 1 / 0)
    p = _after(calls, "pkg/core.py", old_string="x", new_string="    return 1")
    assert post_tool_hook.run(p) == 0
    assert capsys.readouterr().out == ""


def test_cli_dispatches_hook_edit():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gingugu.server import main; "
            "sys.argv=['gingugu','hook','edit']; main()",
        ],
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


# --- gingugu init -----------------------------------------------------------------


def _scripts(groups):
    return {g["hooks"][0]["command"].rsplit("/", 1)[-1]: g for g in groups}


def test_init_wires_a_posttooluse_entry_for_edits():
    settings, _, _ = merge_settings({})
    by_script = _scripts(settings["hooks"]["PostToolUse"])
    assert set(by_script["post_edit_xray.py"]["matcher"].split("|")) == {
        "Edit",
        "Write",
        "MultiEdit",
    }
    assert set(by_script["post_tool_xray.py"]["matcher"].split("|")) == {"Read", "Grep", "Glob"}


def test_init_adds_the_edit_entry_to_an_already_initialised_repo():
    old, _, _ = merge_settings({})
    old["hooks"]["PostToolUse"] = [
        g for g in old["hooks"]["PostToolUse"] if "post_edit_xray.py" not in json.dumps(g)
    ]
    settings, _, _ = merge_settings(old)
    assert "post_edit_xray.py" in json.dumps(settings["hooks"]["PostToolUse"])
    assert json.dumps(settings).count("post_tool_xray.py") == 1


def test_init_installs_the_doorway(tmp_path):
    init_main(["--path", str(tmp_path)])
    script = tmp_path / ".claude" / "hooks" / "post_edit_xray.py"
    text = script.read_text()
    assert "gingugu-init:managed-file" in text
    assert '"edit"' in text


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
    return tmp_path / ".claude" / "hooks" / "post_edit_xray.py", log, env


def _door(script, payload, env) -> str:
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    return proc.stdout


@posix_only
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_the_doorway_passes_an_edit(doorway, tool):
    script, log, env = doorway
    assert _door(script, {"tool_name": tool, "tool_input": {}}, env).strip() == '{"ok": 1}'
    assert log.read_text().split() == ["hook", "edit"]


@posix_only
def test_the_doorway_drops_other_tools_and_garbage_before_loading_gingugu(doorway):
    script, log, env = doorway
    assert _door(script, {"tool_name": "Read", "tool_input": {}}, env) == ""
    assert _door(script, "nope", env) == ""
    assert not log.exists()
