"""scripts/live_rig.py: a throwaway Claude Code project on THIS checkout.

What it must never get wrong is isolation: on a machine in remote mode a scratch
MEMORY_DB_PATH is not enough (remote.json ignores it on purpose), so every
process the rig starts has MEMORY_REMOTE_URL=off. The keychain is the user's
real one, so credentials stay off unless asked for.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "live_rig.py"


def _rig():
    spec = importlib.util.spec_from_file_location("_script_live_rig", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_rig_is_this_checkouts_doorway_hook_and_server_on_a_scratch_brain(tmp_path):
    rig = _rig()
    repo = _SCRIPT.parent.parent
    built = rig.build(tmp_path / "rig", repo)
    doorway = built.root / ".claude" / "hooks" / "pre_tool_tripwire.py"
    template = repo / "src/gingugu/bootstrap/templates/pre_tool_tripwire.py.tmpl"
    assert doorway.read_text() == template.read_text()

    settings = json.loads((built.root / ".claude" / "settings.json").read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert str(doorway) in command

    server = json.loads(built.mcp_config.read_text())["mcpServers"]["gingugu"]
    assert server["command"] == str(repo / ".venv" / "bin" / "gingugu")
    for env in (server["env"], built.env):
        assert env["MEMORY_REMOTE_URL"] == "off"
        assert env["MEMORY_DB_PATH"] == str(built.db)
        assert env["MEMORY_CREDENTIALS_ENABLED"] == "false"
    assert built.db.parent.is_relative_to(built.root)
    assert built.env["PATH"].split(":")[0] == str(repo / ".venv" / "bin")


def test_credentials_only_when_asked(tmp_path):
    built = _rig().build(tmp_path / "rig", _SCRIPT.parent.parent, credentials=True)
    assert built.env["MEMORY_CREDENTIALS_ENABLED"] == "true"


def test_the_command_is_bypass_mode_on_project_settings_only(tmp_path):
    rig = _rig()
    built = rig.build(tmp_path / "rig", _SCRIPT.parent.parent)
    argv = rig.command(built, mode="bypassPermissions", prompt=None, model=None)
    assert argv[:2] == ["claude", "--permission-mode"] and argv[2] == "bypassPermissions"
    assert "--strict-mcp-config" in argv and "--setting-sources" in argv
    assert "-p" not in argv
    headless = rig.command(built, mode="auto", prompt="do it", model="haiku")
    assert headless[headless.index("-p") + 1] == "do it"
    assert "stream-json" in headless and "haiku" in headless


def test_the_summary_reads_denials_and_the_scratch_brain(tmp_path):
    rig = _rig()
    stream = [
        {"type": "system", "subtype": "init", "permissionMode": "bypassPermissions"},
        {
            "type": "system",
            "subtype": "permission_denied",
            "tool_name": "mcp__gingugu__memory_namespaces",
            "decision_reason_type": "hook",
            "decision_reason": "gingugu: delete namespace",
        },
        {"type": "result", "subtype": "success", "permission_denials": [{"tool_name": "x"}]},
    ]
    lines = rig.summarize([json.dumps(m) for m in stream] + ["not json"], tmp_path / "none.db")
    text = "\n".join(lines)
    assert "bypassPermissions" in text
    assert "denied mcp__gingugu__memory_namespaces (hook): gingugu: delete namespace" in text
    # No scratch brain written: say so loudly, it means the writes went elsewhere.
    assert "NOT ISOLATED" in text or "no scratch brain" in text
