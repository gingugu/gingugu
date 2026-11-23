"""``memory_promote``: hand-picked gold, by id, into the crew commons.

On a brain that holds the crew (the owner's - the Pi), promotion is a copy
inside one database. The filter is a net, not the judge: the caller picks
the ids, and the net still holds back what must never leave (unverified,
personal or session tags, secret-shaped text, the caller's own persona
namespace). A copy is matched to its source by the source's id, so promoting
an unchanged memory twice is a no-op and promoting an edited one supersedes
the earlier copy instead of piling up beside it.
"""

from __future__ import annotations

import json

import pytest

from gingugu.models import Confidence, MemoryType


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "brain.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "work")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv("MEMORY_CREW_URL", "off")
    monkeypatch.delenv("MEMORY_REMOTE_URL", raising=False)
    from gingugu.server import build

    mcp, ctx = build()
    ctx.namespaces.get_or_create("crew")
    return mcp, ctx


def _add(ctx, ns="work", title="verify the tag", content="check it exists", **kw):
    ns_id = ctx.namespaces.get_or_create(ns).id
    kw.setdefault("confidence", Confidence.VERIFIED)
    kw.setdefault("type", MemoryType.PATTERN)
    return ctx.store.create(namespace_id=ns_id, title=title, content=content, **kw).id


async def _call(mcp, tool, **args) -> dict:
    result = await mcp.call_tool(tool, args)
    return json.loads(result.content[0].text)


def _crew(ctx, *, deprecated=False):
    ns = ctx.namespaces.get_or_create("crew")
    rows = ctx.conn.execute(
        "SELECT id, title, content, confidence, metadata FROM memories WHERE namespace_id = ?",
        (ns.id,),
    ).fetchall()
    return [dict(r) for r in rows if deprecated or r["confidence"] != "deprecated"]


async def test_a_picked_memory_is_copied_into_crew_stamped(server):
    mcp, ctx = server
    src = _add(ctx)
    out = await _call(mcp, "memory_promote", ids=src)
    assert out["ok"] and [p["status"] for p in out["promoted"]] == ["promoted"]
    [copy] = _crew(ctx)
    stamp = json.loads(copy["metadata"])["promoted_from"]
    assert stamp["id"] == src and stamp["namespace"] == "work"
    assert stamp["contributor"] == "beepboop"
    assert "promoted" in ctx.store.get_tags(copy["id"])


async def test_only_the_picked_ids_go_up(server):
    mcp, ctx = server
    picked = _add(ctx, title="picked")
    _add(ctx, title="left alone")
    await _call(mcp, "memory_promote", ids=picked)
    assert [m["title"] for m in _crew(ctx)] == ["picked"]


async def test_promoting_an_unchanged_memory_twice_is_a_no_op(server):
    mcp, ctx = server
    src = _add(ctx)
    await _call(mcp, "memory_promote", ids=src)
    again = await _call(mcp, "memory_promote", ids=src)
    assert [p["status"] for p in again["promoted"]] == ["unchanged"]
    assert len(_crew(ctx, deprecated=True)) == 1


async def test_promoting_an_edited_memory_supersedes_the_old_copy(server):
    mcp, ctx = server
    src = _add(ctx)
    first = await _call(mcp, "memory_promote", ids=src)
    old_copy = first["promoted"][0]["copy_id"]
    ctx.store.update(src, content="check it exists, in the registry you deploy from")
    second = await _call(mcp, "memory_promote", ids=src)
    [entry] = second["promoted"]
    assert entry["status"] == "updated" and entry["superseded"] == [old_copy]
    [live] = _crew(ctx)
    assert "registry" in live["content"]
    old = ctx.store.get(old_copy, record_access=False)
    assert old.confidence == Confidence.DEPRECATED
    edges = ctx.conn.execute(
        "SELECT relation_type FROM relations WHERE source_id = ? AND target_id = ?",
        (live["id"], old_copy),
    ).fetchall()
    assert [e[0] for e in edges] == ["supersedes"]


@pytest.mark.parametrize(
    "kw, reason",
    [
        ({"confidence": Confidence.INFERRED}, "verified"),
        ({"tags": ["reflection"]}, "reflection"),
        ({"tags": ["Beepboop"]}, "beepboop"),
        ({"content": "token: abcdefghijklmnop1234"}, "secret"),
    ],
)
async def test_the_net_still_holds_back_what_must_stay_home(server, kw, reason):
    mcp, ctx = server
    src = _add(ctx, **kw)
    out = await _call(mcp, "memory_promote", ids=src)
    assert out["promoted"] == []
    [held] = out["held"]
    assert held["id"] == src and reason in held["reason"].lower()
    assert _crew(ctx) == []


async def test_the_callers_persona_namespace_stays_home(server):
    mcp, ctx = server
    src = _add(ctx, ns="beepboop", title="who I am")
    out = await _call(mcp, "memory_promote", ids=src)
    assert out["held"][0]["reason"].startswith("your persona namespace")


async def test_a_crew_memory_is_not_promoted_onto_itself(server):
    mcp, ctx = server
    src = _add(ctx, ns="crew")
    out = await _call(mcp, "memory_promote", ids=src)
    assert "already in crew" in out["held"][0]["reason"]


async def test_unknown_ids_are_reported_missing(server):
    mcp, ctx = server
    src = _add(ctx)
    out = await _call(mcp, "memory_promote", ids=f"{src},nope")
    assert out["missing"] == ["nope"] and len(out["promoted"]) == 1


async def test_dry_run_reports_and_writes_nothing(server):
    mcp, ctx = server
    src = _add(ctx)
    out = await _call(mcp, "memory_promote", ids=src, dry_run=True)
    assert out["dry_run"] and [p["status"] for p in out["promoted"]] == ["would_promote"]
    assert _crew(ctx) == []


async def test_a_brain_without_a_crew_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "solo.db"))
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_CREW_URL", "off")
    from gingugu.server import build

    mcp, ctx = build()
    src = _add(ctx)
    out = await _call(mcp, "memory_promote", ids=src)
    assert not out["ok"] and "crew" in out["error"]
    assert ctx.namespaces.get("crew") is None


async def test_no_ids_is_an_error(server):
    mcp, _ = server
    out = await _call(mcp, "memory_promote", ids=" , ")
    assert not out["ok"]


async def test_a_secret_in_about_is_held_too(server):
    mcp, ctx = server
    src = _add(ctx, about="api_key: abcdefghijklmnop1234")
    out = await _call(mcp, "memory_promote", ids=src)
    assert out["promoted"] == [] and "secret" in out["held"][0]["reason"]


async def test_a_stamped_store_still_meets_the_net(server):
    mcp, ctx = server
    meta = json.dumps({"promoted_from": {"id": "src-9"}})
    out = await _call(
        mcp,
        "memory_store",
        title="t",
        content="c",
        type="fact",
        confidence="inferred",
        namespace="crew",
        metadata=meta,
    )
    assert not out["ok"] and "not verified" in out["error"]
    assert _crew(ctx) == []


async def test_a_capability_keeps_its_run_block_when_promoted(server):
    mcp, ctx = server
    meta = json.dumps({"capability": {"run": "scripts/fleet.py", "path": "scripts"}})
    src = _add(ctx, type=MemoryType.CAPABILITY, metadata=meta)
    await _call(mcp, "memory_promote", ids=src)
    [copy] = _crew(ctx)
    stored = json.loads(copy["metadata"])
    assert stored["capability"]["run"] == "scripts/fleet.py"
    assert stored["promoted_from"]["id"] == src
