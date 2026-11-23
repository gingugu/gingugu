"""``memory_promote`` from a crew mate, and what the receiving brain does with it.

A mate's brain is local: ``memory_promote`` there pushes up the crew link on
the link's scoped token. The receiving brain does the matching itself, because
only it can read the provenance stamps (search never returns metadata): a
memory stored with ``promoted_from`` is matched on the source id AND the
contributor the receiving server derives from the token, so one mate cannot
supersede another's promotion by forging a stamp.

Also here: ``memory_context`` drops a crew copy when its source is already in
the load, so an owner loading ``crow`` and ``crew`` sees a promoted memory once.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gingugu.models import Confidence, MemoryType
from gingugu.promote import _call, _session
from gingugu.remote import RemoteTarget
from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import make_brain, serving


@pytest.fixture
async def crew_brain(tmp_path, monkeypatch):
    (tmp_path / "pi").mkdir()
    b = make_brain(tmp_path / "pi", monkeypatch, credentials=False)
    tokens = TokenStore(tmp_path / "pi" / "serve_tokens.json")
    polly = tokens.add("polly", {"crew": "write"})
    rex = tokens.add("rex", {"crew": "write"})
    async with serving(b.app()) as served:
        yield served.url, polly, rex, tmp_path / "pi" / "brain.db"


def _stamped(title, content, src_id, contributor):
    meta = {"promoted_from": {"id": src_id, "namespace": "work", "contributor": contributor}}
    return {
        "title": title,
        "content": content,
        "type": "pattern",
        "confidence": "verified",
        "namespace": "crew",
        "tags": "promoted",
        "metadata": json.dumps(meta),
    }


async def _store(url, token, **args) -> dict:
    async with _session(f"{url}/mcp", token) as s:
        return await _call(s, "memory_store", args)


def _rows(db):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT m.id, m.title, m.content, m.confidence, m.metadata FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id WHERE n.name = 'crew'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def test_a_stamped_store_is_matched_on_its_source_id(crew_brain):
    url, polly, _, db = crew_brain
    first = await _store(url, polly, **_stamped("t", "v1", "src-1", "polly"))
    same = await _store(url, polly, **_stamped("t", "v1", "src-1", "polly"))
    assert same["promotion"]["status"] == "unchanged"
    assert same["memory"]["id"] == first["memory"]["id"]
    edited = await _store(url, polly, **_stamped("t", "v2", "src-1", "polly"))
    assert edited["promotion"]["status"] == "updated"
    live = [r for r in _rows(db) if r["confidence"] != "deprecated"]
    assert [r["content"] for r in live] == ["v2"]


async def test_the_contributor_is_the_token_not_the_stamp(crew_brain):
    url, polly, rex, db = crew_brain
    await _store(url, polly, **_stamped("t", "v1", "src-1", "polly"))
    # rex forges polly's stamp on the same source id: a new memory, nothing superseded
    forged = await _store(url, rex, **_stamped("t", "hijack", "src-1", "polly"))
    assert forged["promotion"]["status"] == "promoted"
    live = {r["content"] for r in _rows(db) if r["confidence"] != "deprecated"}
    assert live == {"v1", "hijack"}
    [rex_row] = [r for r in _rows(db) if r["content"] == "hijack"]
    assert json.loads(rex_row["metadata"])["promoted_from"]["contributor"] == "rex"


@pytest.fixture
def mate(tmp_path, monkeypatch, crew_brain):
    url, polly, _, db = crew_brain
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "mate.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "work")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PERSONA", "polly")
    monkeypatch.delenv("MEMORY_REMOTE_URL", raising=False)
    from gingugu import crew

    monkeypatch.setattr(crew, "active", lambda: RemoteTarget(url, "machine"))
    monkeypatch.setattr(crew, "token", lambda target: polly)
    from gingugu.server import build

    mcp, ctx = build()
    ns = ctx.namespaces.get_or_create("work")
    src = ctx.store.create(
        namespace_id=ns.id,
        type=MemoryType.BUG,
        title="proxy certs",
        content="trust the OS keychain",
        confidence=Confidence.VERIFIED,
    )
    return mcp, ctx, src.id, db


async def _promote(mcp, **args) -> dict:
    result = await mcp.call_tool("memory_promote", args)
    return json.loads(result.content[0].text)


async def test_a_mate_pushes_up_the_crew_link(mate):
    mcp, ctx, src, db = mate
    out = await _promote(mcp, ids=src)
    assert out["ok"] and [p["status"] for p in out["promoted"]] == ["promoted"]
    [row] = _rows(db)
    stamp = json.loads(row["metadata"])["promoted_from"]
    assert stamp["id"] == src and stamp["contributor"] == "polly"
    assert ctx.namespaces.get("crew") is None  # nothing copied into the mate's own brain


async def test_a_mate_re_promoting_an_edit_supersedes_upstream(mate):
    mcp, ctx, src, db = mate
    await _promote(mcp, ids=src)
    assert [p["status"] for p in (await _promote(mcp, ids=src))["promoted"]] == ["unchanged"]
    ctx.store.update(src, content="trust the OS keychain; never disable verification")
    out = await _promote(mcp, ids=src)
    assert [p["status"] for p in out["promoted"]] == ["updated"]
    live = [r for r in _rows(db) if r["confidence"] != "deprecated"]
    assert len(live) == 1 and "never disable" in live[0]["content"]


async def test_a_mate_dry_run_sends_nothing(mate):
    mcp, _, src, db = mate
    out = await _promote(mcp, ids=src, dry_run=True)
    assert [p["status"] for p in out["promoted"]] == ["would_promote"]
    assert _rows(db) == []


async def test_context_drops_a_crew_copy_whose_source_is_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "owner.db"))
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv("MEMORY_CREW_URL", "off")
    from gingugu.server import build

    mcp, ctx = build()
    ctx.namespaces.get_or_create("crew")
    ns = ctx.namespaces.get_or_create("crow")
    src = ctx.store.create(
        namespace_id=ns.id,
        type=MemoryType.PATTERN,
        title="verify the tag",
        content="check the registry",
        confidence=Confidence.VERIFIED,
    )
    await _promote(mcp, ids=src.id)
    both = json.loads(
        (await mcp.call_tool("memory_context", {"namespace": "crow,crew"})).content[0].text
    )
    titles = [m["title"] for m in both["memories"]]
    assert titles.count("verify the tag") == 1
    assert [m["namespace"] for m in both["memories"] if m["title"] == "verify the tag"] == ["crow"]
    # Loaded alone, the copy is all there is - and it stays.
    crew_only = json.loads(
        (await mcp.call_tool("memory_context", {"namespace": "crew"})).content[0].text
    )
    assert [m["title"] for m in crew_only["memories"]] == ["verify the tag"]


async def test_a_fenced_server_never_pushes_on_the_links_token(mate):
    mcp, ctx, src, db = mate
    from gingugu.grants import Grant

    ctx.stdio_grant = Grant("MEMORY_GRANT", {"work": "read", "minions": "write"})
    out = await _promote(mcp, ids=src)
    assert not out["ok"] and "fenced" in out["error"]
    assert _rows(db) == []
