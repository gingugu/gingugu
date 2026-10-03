"""The brain's side of remote-mode hooks: ``/hook/recall``, ``/hook/tripwires``,
``/hook/trip`` and ``/hook/warmup`` on ``gingugu serve``.

Owner tokens only, like ``/token/derive``: these serve the machine's own hooks,
which run with the main thread's full view. The brain embeds (the model is held
warm there), reads read-only, and keeps no session state - suppression arrives
with each request. The only write is the ``query_log`` row each hook made locally.
"""

from __future__ import annotations

import json
import sqlite3

import anyio
import httpx
import pytest

from gingugu import prompt_hook, tripwire
from gingugu.database import Database
from gingugu.grants import Grant
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore
from tests.proxy_fixtures import Served, free_port, make_brain
from tests.test_embeddings import FakeEmbedder

MARKER = "zq-hidden-7781"
OPEN = {"bar": 0.0, "margin": 1.0, "cap": 3, "require_lexical": False}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    b = make_brain(tmp_path, monkeypatch, credentials=False)
    database = Database(tmp_path / "brain.db")
    conn = database.connect()
    from gingugu.config import load_config

    nsm = NamespaceManager(conn, load_config())
    store = MemoryStore(conn, embedder=FakeEmbedder())
    crow = nsm.get_or_create("crow")
    hidden = nsm.get_or_create("hidden-ns")
    mem = store.create(
        namespace_id=crow.id,
        type=MemoryType.DECISION,
        title="Proxy replays initialize after a restart",
        content="alpha alpha reconnect",
    )
    store.create(
        namespace_id=hidden.id,
        type=MemoryType.DECISION,
        title=f"{MARKER} alpha",
        content=f"alpha alpha {MARKER}",
    )
    tripwire.add_tripwire(conn, mem.id, "Bash", r"gh pr merge.*--delete-branch")
    conn.commit()
    database.close()
    b.mem_id = mem.id
    return b


@pytest.fixture
async def served(brain):
    s = Served(free_port())
    await s.start(brain.app())
    try:
        yield s
    finally:
        await s.stop()


async def _post(served, path, body, token):
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.post(
            served.url + path, json=body, headers={"Authorization": f"Bearer {token}"}
        )


def _recall_body(**over):
    body = {
        "prompt": "alpha: how does the proxy come back after the brain restarts",
        "session_id": "cc-9",
        "namespaces": ["crow", "gingugu"],
        "suppressed": [],
        "config": OPEN,
    }
    body.update(over)
    return body


def _log(brain) -> list[tuple]:
    conn = sqlite3.connect(brain.tmp_path / "brain.db")
    try:
        return conn.execute("SELECT tool, query, result_ids, session_id FROM query_log").fetchall()
    finally:
        conn.close()


async def test_recall_ranks_on_the_brain_and_logs_the_prompt(brain, served):
    r = await _post(served, "/hook/recall", _recall_body(), brain.machine)
    assert r.status_code == 200
    body = r.json()
    assert body["ids"] == [brain.mem_id]
    assert "Proxy replays initialize after a restart" in body["context"]
    assert MARKER not in json.dumps(body)  # hidden-ns was never asked for
    ((tool, query, ids, session),) = _log(brain)
    assert tool == "hook" and session == "cc-9"
    assert "proxy come back" in query and json.loads(ids) == [brain.mem_id]


async def test_recall_honours_the_suppression_it_is_sent(brain, served):
    r = await _post(served, "/hook/recall", _recall_body(suppressed=[brain.mem_id]), brain.machine)
    assert r.status_code == 200
    assert r.json() == {"context": None, "ids": []}


async def test_recall_uses_the_brains_gate_when_none_is_sent(brain, served):
    body = _recall_body()
    del body["config"]
    r = await _post(served, "/hook/recall", body, brain.machine)
    assert r.status_code == 200  # the real bar may or may not pass; it must answer


@pytest.mark.parametrize(
    "over",
    [
        {"prompt": 7},
        {"namespaces": "crow"},
        {"namespaces": ["../etc"]},
        {"namespaces": [f"n{i}" for i in range(40)]},
        {"suppressed": "m1"},
        {"suppressed": [1]},
        {"config": {"bar": "high"}},
        {"config": {"cap": True}},
        {"session_id": 3},
    ],
)
async def test_recall_refuses_a_malformed_body(brain, served, over):
    r = await _post(served, "/hook/recall", _recall_body(**over), brain.machine)
    assert r.status_code == 400
    assert _log(brain) == []


async def test_recall_refuses_a_non_object_body(brain, served):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            served.url + "/hook/recall",
            content=b"[1]",
            headers={"Authorization": f"Bearer {brain.machine}"},
        )
    assert r.status_code == 400


@pytest.mark.parametrize("path", ["/hook/recall", "/hook/tripwires", "/hook/trip", "/hook/warmup"])
async def test_only_an_owner_token_may_use_a_hook_route(brain, served, path):
    derived = brain.derived.mint(Grant("derived:x", {"crow": "write"}, derived=True), 60)
    r = await _post(served, path, {}, derived)
    assert r.status_code == 403
    r = await _post(served, path, {}, "not-a-token")
    assert r.status_code == 401


async def test_tripwire_rules_come_back_for_the_namespaces_asked(brain, served):
    r = await _post(served, "/hook/tripwires", {"namespaces": ["crow"]}, brain.machine)
    assert r.status_code == 200
    (wire,) = r.json()["tripwires"]
    assert wire["memory_id"] == brain.mem_id
    assert wire["input_pattern"] == r"gh pr merge.*--delete-branch"
    assert tripwire.Tripwire(**wire).title == "Proxy replays initialize after a restart"
    r = await _post(served, "/hook/tripwires", {"namespaces": ["elsewhere"]}, brain.machine)
    assert r.json() == {"tripwires": []}


async def test_a_trip_is_logged_on_the_brain(brain, served):
    body = {"session_id": "cc-9", "text": "gh pr merge 1 --delete-branch", "ids": [brain.mem_id]}
    r = await _post(served, "/hook/trip", {**body, "namespaces": ["crow"]}, brain.machine)
    assert r.status_code == 200
    ((tool, query, ids, session),) = _log(brain)
    assert tool == "tripwire" and query.startswith("gh pr merge") and session == "cc-9"


@pytest.mark.parametrize("body", [{"ids": "x"}, {"text": 1}, {"namespaces": [None]}])
async def test_a_malformed_trip_is_refused(brain, served, body):
    full = {"session_id": "s", "text": "t", "ids": [], "namespaces": ["crow"], **body}
    r = await _post(served, "/hook/trip", full, brain.machine)
    assert r.status_code == 400


def _warm(spec="crow=read,minions=write", **over):
    body = {"spec": spec, "task_hint": "alpha reconnect", "agent_type": "probe"}
    body["namespaces"] = ["crow", "gingugu"]
    body.update(over)
    return body


async def test_warmup_reads_inside_the_agents_grant(brain, served):
    r = await _post(served, "/hook/warmup", _warm(), brain.machine)
    assert r.status_code == 200
    context = r.json()["context"]
    assert "Proxy replays initialize after a restart" in context
    assert "minion `probe`" in context
    assert MARKER not in context


async def test_a_wildcard_warmup_stays_inside_the_sent_namespaces(brain, served):
    r = await _post(served, "/hook/warmup", _warm(spec="*=read"), brain.machine)
    assert MARKER not in json.dumps(r.json())
    r = await _post(served, "/hook/warmup", _warm(spec="*=read", namespaces=["x"]), brain.machine)
    assert r.json() == {"context": None}


@pytest.mark.parametrize("spec", ["*=write", "", "crow=admin"])
async def test_a_bad_or_full_grant_gets_no_warmup(brain, served, spec):
    r = await _post(served, "/hook/warmup", _warm(spec=spec), brain.machine)
    assert r.status_code == 200
    assert r.json() == {"context": None}


@pytest.mark.parametrize(
    "over", [{"spec": 1}, {"agent_type": ""}, {"task_hint": 5}, {"namespaces": "crow"}]
)
async def test_a_malformed_warmup_is_refused(brain, served, over):
    r = await _post(served, "/hook/warmup", _warm(**over), brain.machine)
    assert r.status_code == 400


async def test_the_prompt_hook_end_to_end_against_a_live_brain(
    brain, served, tmp_path, monkeypatch, capsys
):
    client_data = tmp_path / "client"
    monkeypatch.setenv("MEMORY_REMOTE_URL", served.url)
    monkeypatch.setattr("gingugu.remote._kr_get", lambda url: brain.machine)
    monkeypatch.setenv("MEMORY_RECALL_HOOK_BAR", "0")
    monkeypatch.setenv("MEMORY_RECALL_HOOK_LEXICAL", "0")
    payload = {
        "prompt": "alpha: how does the proxy come back after the brain restarts",
        "session_id": "cc-e2e",
        "cwd": str(client_data / "gingugu"),
    }
    assert await anyio.to_thread.run_sync(prompt_hook.run, payload) == 0
    out = json.loads(capsys.readouterr().out)
    assert "Proxy replays initialize" in out["hookSpecificOutput"]["additionalContext"]
    assert await anyio.to_thread.run_sync(prompt_hook.run, payload) == 0
    assert capsys.readouterr().out == ""  # suppressed by this machine's own state
