"""`gingugu init` wiring for warm minions: the doorways and settings entries.

Two doorways matter. The new SubagentStart one hands the event to
``gingugu hook subagent``. The existing PreToolUse tripwire doorway drops the
``mcp__gingugu__*`` tools in stdlib before loading the package - right for the
main thread, where those tools must never trip, but inside a subagent those
are exactly the inherited full-brain writes the minion fence has to see.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from gingugu.bootstrap import main as init_main
from gingugu.bootstrap.settings import merge_settings

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake gingugu is a shell script")


def test_every_repo_agent_is_fenced_with_a_real_grant():
    """This repo's own minions: inline fenced server, inherited brain stripped."""
    from pathlib import Path

    from gingugu.handlers.fence import stdio_grant
    from gingugu.subagent_hook import agent_grant

    root = Path(__file__).resolve().parent.parent
    agents = sorted((root / ".claude" / "agents").glob("*.md"))
    assert agents
    for path in agents:
        keys = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
        spec = agent_grant(keys["name"].strip(), [root])
        assert spec, path
        assert not stdio_grant(spec).is_full, path
        assert "mcp__gingugu" in keys["disallowedTools"], path
        assert "mcp__brain" in keys["tools"], path


def test_init_wires_the_subagentstart_hook():
    settings, added, _ = merge_settings({})
    assert "SubagentStart" in added
    (group,) = settings["hooks"]["SubagentStart"]
    assert group["matcher"] == ""
    hook = group["hooks"][0]
    assert "subagent_warmup.py" in hook["command"]
    assert hook["timeout"] <= 20


def test_init_installs_the_warmup_doorway(tmp_path):
    init_main(["--path", str(tmp_path)])
    script = tmp_path / ".claude" / "hooks" / "subagent_warmup.py"
    assert script.exists()
    assert '"subagent"' in script.read_text()


# --- the mcp__brain permission ----------------------------------------------
#
# A fenced minion's calls go to its own `brain` server. In a repo that does not
# allow `mcp__brain`, every one of them is permission-denied (or prompts), so
# whatever installs the fenced agents also installs the allow.


def test_merge_permissions_adds_the_allow_once_and_keeps_the_rest():
    from gingugu.bootstrap.settings import MINION_ALLOW, merge_permissions

    settings = {"permissions": {"allow": ["Read"], "deny": ["Bash(rm *)"]}}
    assert merge_permissions(settings) is True
    assert settings["permissions"]["allow"] == ["Read", MINION_ALLOW]
    assert settings["permissions"]["deny"] == ["Bash(rm *)"]
    assert merge_permissions(settings) is False
    assert settings["permissions"]["allow"].count(MINION_ALLOW) == 1


def test_merge_permissions_creates_the_block_when_missing():
    from gingugu.bootstrap.settings import MINION_ALLOW, merge_permissions

    settings: dict = {}
    assert merge_permissions(settings) is True
    assert settings == {"permissions": {"allow": [MINION_ALLOW]}}


@pytest.mark.parametrize("odd", [{"permissions": "x"}, {"permissions": {"allow": "Read"}}])
def test_merge_permissions_respects_an_unexpected_shape(odd):
    from gingugu.bootstrap.settings import merge_permissions

    before = json.loads(json.dumps(odd))
    assert merge_permissions(odd) is False
    assert odd == before


def test_init_allows_brain_in_the_repo_settings(tmp_path):
    from gingugu.bootstrap.settings import MINION_ALLOW

    init_main(["--path", str(tmp_path)])
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert MINION_ALLOW in settings["permissions"]["allow"]


def test_init_allows_an_existing_repo_that_is_already_hooked(tmp_path):
    from gingugu.bootstrap.settings import MINION_ALLOW

    init_main(["--path", str(tmp_path)])
    path = tmp_path / ".claude" / "settings.json"
    settings = json.loads(path.read_text())
    settings["permissions"]["allow"].remove(MINION_ALLOW)
    path.write_text(json.dumps(settings))
    init_main(["--path", str(tmp_path)])
    assert MINION_ALLOW in json.loads(path.read_text())["permissions"]["allow"]


def test_init_allows_brain_in_the_user_settings(tmp_path, sandboxed_user_settings):
    from gingugu.bootstrap.settings import MINION_ALLOW

    user = sandboxed_user_settings
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "theme": "dark"}))
    init_main(["--path", str(tmp_path)])
    after = json.loads(user.read_text())
    assert after["permissions"]["allow"] == ["Read", MINION_ALLOW]
    assert after["theme"] == "dark"
    assert json.loads(user.with_name("settings.json.bak").read_text())["theme"] == "dark"
    init_main(["--path", str(tmp_path)])  # idempotent
    assert json.loads(user.read_text())["permissions"]["allow"].count(MINION_ALLOW) == 1


def test_init_dry_run_leaves_user_settings_alone(tmp_path, sandboxed_user_settings):
    user = sandboxed_user_settings
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("{}")
    init_main(["--path", str(tmp_path), "--dry-run"])
    assert user.read_text() == "{}"


def test_init_leaves_unparseable_user_settings_alone(tmp_path, sandboxed_user_settings):
    user = sandboxed_user_settings
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("{not json")
    init_main(["--path", str(tmp_path)])
    assert user.read_text() == "{not json"


def test_cli_dispatches_hook_subagent():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gingugu.server import main; "
            "sys.argv=['gingugu','hook','subagent']; main()",
        ],
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


@pytest.fixture
def doorways(tmp_path):
    """Installed doorways plus a fake `gingugu` on PATH that logs its calls."""
    init_main(["--path", str(tmp_path)])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fake = bin_dir / "gingugu"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncat > /dev/null\necho \'{{"ok": 1}}\'\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return tmp_path / ".claude" / "hooks", log, env


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
def test_tripwire_doorway_passes_a_minions_gingugu_call(doorways):
    hooks, log, env = doorways
    payload = {"tool_name": "mcp__gingugu__memory_store", "tool_input": {}, "agent_id": "a1"}
    assert _door(hooks / "pre_tool_tripwire.py", payload, env).strip() == '{"ok": 1}'
    assert log.read_text().split() == ["hook", "tool"]


@posix_only
def test_tripwire_doorway_still_drops_the_main_threads_gingugu_call(doorways):
    hooks, log, env = doorways
    payload = {"tool_name": "mcp__gingugu__memory_store", "tool_input": {}}
    assert _door(hooks / "pre_tool_tripwire.py", payload, env) == ""
    assert not log.exists()


@posix_only
def test_warmup_doorway_hands_subagentstart_to_the_package(doorways):
    hooks, log, env = doorways
    payload = {"hook_event_name": "SubagentStart", "agent_type": "probe", "agent_id": "a1"}
    assert _door(hooks / "subagent_warmup.py", payload, env).strip() == '{"ok": 1}'
    assert log.read_text().split() == ["hook", "subagent"]


@posix_only
def test_warmup_doorway_skips_a_payload_without_an_agent_type(doorways):
    hooks, log, env = doorways
    assert _door(hooks / "subagent_warmup.py", {"hook_event_name": "SubagentStart"}, env) == ""
    assert not log.exists()
