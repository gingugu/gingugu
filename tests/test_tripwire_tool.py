"""The ``memory_tripwire`` MCP tool and the wiring that installs the hook."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from gingugu.bootstrap.settings import merge_settings


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "tw.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "tw")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _call(server, **args) -> dict:
    return _payload(await server.call_tool("memory_tripwire", args))


async def _mem(server) -> str:
    res = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "No co-author trailer",
                "content": "Never sign as Claude.",
                "type": "preference",
            },
        )
    )
    return res["memory"]["id"]


async def test_add_list_remove(server):
    mid = await _mem(server)
    added = await _call(server, action="add", memory_id=mid, tool="Bash", pattern="Co-Authored-By")
    assert added["ok"] and added["tripwire"]["memory_id"] == mid
    wid = added["tripwire"]["id"]

    listed = await _call(server, action="list", memory_id=mid)
    assert listed["ok"] and listed["count"] == 1
    assert listed["tripwires"][0]["title"] == "No co-author trailer"

    by_ns = await _call(server, action="list", namespace="tw")
    assert [w["id"] for w in by_ns["tripwires"]] == [wid]

    removed = await _call(server, action="remove", tripwire_id=wid)
    assert removed == {"ok": True, "removed": wid}
    assert (await _call(server, action="list", memory_id=mid))["count"] == 0


async def test_add_persists_across_connections(server, tmp_path):
    import sqlite3

    mid = await _mem(server)
    await _call(server, action="add", memory_id=mid, tool="Bash", pattern="x")
    conn = sqlite3.connect(tmp_path / "tw.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM tripwires").fetchone()[0] == 1
    finally:
        conn.close()


async def test_test_action_reports_what_would_trip_without_tripping(server):
    mid = await _mem(server)
    await _call(server, action="add", memory_id=mid, tool="Bash", pattern="Co-Authored-By")
    hit = await _call(
        server,
        action="test",
        tool="Bash",
        input=json.dumps({"command": "git commit -m 'x\n\nCo-Authored-By: Claude'"}),
        namespace="tw",
    )
    assert hit["ok"] and [w["memory_id"] for w in hit["matches"]] == [mid]
    miss = await _call(
        server, action="test", tool="Bash", input=json.dumps({"command": "ls"}), namespace="tw"
    )
    assert miss["ok"] and miss["matches"] == []


@pytest.mark.parametrize(
    "args, needle",
    [
        ({"action": "explode"}, "action"),
        ({"action": "add", "tool": "Bash", "pattern": "x"}, "memory_id"),
        ({"action": "add", "memory_id": "nope", "tool": "Bash", "pattern": "x"}, "not found"),
        ({"action": "remove"}, "tripwire_id"),
        ({"action": "remove", "tripwire_id": "nope"}, "not found"),
        ({"action": "test", "tool": "Bash", "input": "{not json"}, "input"),
    ],
)
async def test_errors_are_structured_never_raised(server, args, needle):
    res = await _call(server, **args)
    assert res["ok"] is False
    assert needle in res["error"]


async def test_an_invalid_regex_is_rejected(server):
    mid = await _mem(server)
    res = await _call(server, action="add", memory_id=mid, tool="Bash", pattern="(")
    assert res["ok"] is False


# --- wiring ------------------------------------------------------------------


def test_init_wires_the_pretooluse_hook():
    settings, added, _ = merge_settings({})
    assert "PreToolUse" in added
    (group,) = settings["hooks"]["PreToolUse"]
    assert group["matcher"] == ""
    assert "pre_tool_tripwire.py" in group["hooks"][0]["command"]


def test_init_installs_the_hook_script(tmp_path):
    from gingugu.bootstrap import main

    main(["--path", str(tmp_path)])
    script = tmp_path / ".claude" / "hooks" / "pre_tool_tripwire.py"
    assert script.exists()
    assert "gingugu" in script.read_text() and '"tool"' in script.read_text()


def test_cli_dispatches_hook_tool():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gingugu.server import main; "
            "sys.argv=['gingugu','hook','tool']; main()",
        ],
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
