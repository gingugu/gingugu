"""The scheduling guards over the real MCP tool surface and the real CLI.

Two things here can only be tested end to end. The heartbeat is installed by
wrapping the ``tool`` decorator, so whether it survives contact with FastMCP's
schema generation is a question about the actual server, not about the wrapper.
And ``--if-idle`` is the entire scheduling interface, so its parsing is the one
thing a broken release would silently turn into "never runs" or "always runs".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gingugu.database import Database
from gingugu.dream_cli import _parse_if_idle
from gingugu.dream_cli import main as dream_main


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sched.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(path))
    monkeypatch.setenv("MEMORY_NAMESPACE", "sched")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    return path


@pytest.fixture
def server(db_path):
    from gingugu.server import build_server

    return build_server()


def _set_idle(path, delta: timedelta) -> None:
    db = Database(path)
    db.conn.execute(
        "UPDATE activity SET last_active_at = ? WHERE id = 1",
        ((datetime.now(UTC) - delta).isoformat(),),
    )
    db.conn.commit()
    db.close()


# --- the heartbeat, installed by wrapping the decorator ----------------------


async def test_every_tool_call_stamps_the_heartbeat(server, db_path) -> None:
    _set_idle(db_path, timedelta(hours=5))

    await server.call_tool("memory_stats", {})

    db = Database(db_path)
    row = db.conn.execute("SELECT last_active_at, source FROM activity").fetchone()
    db.close()
    stamped = datetime.fromisoformat(row[0])
    assert (datetime.now(UTC) - stamped).total_seconds() < 30
    assert row[1] == "memory_stats"


async def test_wrapping_preserves_every_tool_signature(server) -> None:
    """The wrapper must be invisible to FastMCP's schema generation.

    ``functools.wraps`` sets ``__wrapped__`` and ``inspect.signature`` follows
    it, which is *why* this works - but "why it should work" is not evidence
    that it does, and a wrapper that silently flattened every tool to
    ``(*args, **kwargs)`` would still register, still run, and hand every
    client an empty parameter list.
    """
    tools = {t.name: t for t in await server.list_tools()}

    assert "memory_store" in tools
    store_params = tools["memory_store"].inputSchema["properties"]
    assert {"title", "content", "type", "namespace", "tags"} <= set(store_params)
    assert set(tools["memory_store"].inputSchema["required"]) == {"content", "title", "type"}

    dream_params = tools["memory_dream"].inputSchema["properties"]
    assert {"action", "proposal_id", "relation_type", "tag"} <= set(dream_params)

    # Docstrings are the tool descriptions clients read; wraps must keep them.
    assert tools["memory_dream"].description
    assert "never writes to" in tools["memory_dream"].description


async def test_a_failing_tool_still_counts_as_activity(server, db_path) -> None:
    """Stamping in a ``finally`` is what keeps a bad patch from reading as idle.

    A session spent hitting an error is a session with a person in it. If only
    successful calls stamped, a run of failures would look exactly like an
    empty room and invite a background pass into the middle of it.
    """
    _set_idle(db_path, timedelta(hours=5))

    res = _payload(await server.call_tool("memory_dream", {"action": "nonsense"}))
    assert res["ok"] is False

    db = Database(db_path)
    row = db.conn.execute("SELECT source FROM activity").fetchone()
    db.close()
    assert row[0] == "memory_dream"


async def test_hand_run_over_the_tool_surface_ignores_idleness(server, db_path) -> None:
    """The tool has no idle gate: calling it *is* the intent."""
    res = _payload(await server.call_tool("memory_dream", {"action": "run"}))

    assert res["ok"] is True
    assert res["outcome"] == "ran"


# --- --if-idle, the whole scheduling interface -------------------------------


@pytest.mark.parametrize(
    ("args", "expected_seconds"),
    [
        ([], None),
        (["--if-idle"], 20 * 60),
        (["--if-idle=45"], 45 * 60),
        (["--if-idle=0"], 0),
        (["--if-idle=0.5"], 30),
    ],
)
def test_if_idle_parsing(args, expected_seconds) -> None:
    remaining, seconds, bad = _parse_if_idle(list(args), default_minutes=20)

    assert bad is False
    assert seconds == expected_seconds
    assert remaining == []


def test_if_idle_keeps_a_namespace_argument() -> None:
    remaining, seconds, bad = _parse_if_idle(["crow", "--if-idle=5"], default_minutes=20)

    assert remaining == ["crow"]
    assert seconds == 300
    assert bad is False


@pytest.mark.parametrize("bad_arg", ["--if-idle=soon", "--if-idle=-5"])
def test_if_idle_rejects_nonsense(bad_arg) -> None:
    """A misparsed threshold must be an error, not a silent default.

    Falling back to the default on a typo is the worst option available: the
    scheduler keeps exiting 0 and the operator never learns their interval was
    ignored.
    """
    _, seconds, bad = _parse_if_idle([bad_arg], default_minutes=20)

    assert bad is True
    assert seconds is None


def test_cli_skips_and_exits_zero_while_active(db_path, capsys) -> None:
    Database(db_path).connect()  # create + migrate
    _set_idle(db_path, timedelta(minutes=1))

    assert dream_main(["--if-idle"]) == 0

    out = capsys.readouterr().out
    assert "skipped" in out
    assert "last used" in out


def test_cli_runs_when_the_brain_is_quiet(db_path, capsys) -> None:
    Database(db_path).connect()
    _set_idle(db_path, timedelta(hours=3))

    assert dream_main(["--if-idle"]) == 0

    out = capsys.readouterr().out
    assert "graph:" in out
    assert "queue:" in out


def test_cli_json_reports_the_outcome(db_path, capsys) -> None:
    Database(db_path).connect()
    _set_idle(db_path, timedelta(minutes=1))

    dream_main(["--if-idle", "--json"])

    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "active"
    assert report["idle_seconds"] > 30


def test_cli_rejects_a_bad_threshold(db_path, capsys) -> None:
    assert dream_main(["--if-idle=nope"]) == 2
    assert "non-negative" in capsys.readouterr().err
