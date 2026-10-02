"""Shared seed for the scoped-token suites: one brain, a hidden namespace.

``hidden`` is the namespace a scoped token must never see. It is also the
server's configured namespace, the worst case: every "omitted namespace"
default points straight at it. Its memories carry ``MARKER`` in their text,
so a leak through any field of any response is one substring check away.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import pytest

from gingugu.grants import FULL, READ, WRITE, Grant
from tests.test_embeddings import FakeEmbedder

MARKER = "ZEBRAFISH-CANARY-7731"
HIDDEN = "hidden-ns-q9"
GRANT = Grant("minion", {"alpha": WRITE, "gamma": READ, "scratch": WRITE})


def payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


async def call(server, tool: str, args: dict) -> dict:
    return payload(await server.call_tool(tool, args))


@dataclass
class Brain:
    server: object
    db_path: str
    ids: dict[str, str] = field(default_factory=dict)
    tripwire_id: str = ""

    def hidden_ids(self) -> list[str]:
        return [v for k, v in self.ids.items() if k.startswith("B")]

    def snapshot(self) -> dict:
        """Every row the hidden namespace owns or touches, byte for byte."""
        conn = sqlite3.connect(self.db_path)
        try:
            ns_id = conn.execute("SELECT id FROM namespaces WHERE name = ?", (HIDDEN,)).fetchone()[
                0
            ]
            ids = self.hidden_ids()
            marks = ",".join("?" * len(ids))
            return {
                "namespace": conn.execute(
                    "SELECT * FROM namespaces WHERE id = ?", (ns_id,)
                ).fetchall(),
                "memories": conn.execute(
                    "SELECT * FROM memories WHERE namespace_id = ? ORDER BY id", (ns_id,)
                ).fetchall(),
                "relations": conn.execute(
                    f"SELECT * FROM relations WHERE source_id IN ({marks}) "
                    f"OR target_id IN ({marks}) ORDER BY id",
                    ids + ids,
                ).fetchall(),
                "tags": conn.execute(
                    f"SELECT * FROM memory_tags WHERE memory_id IN ({marks}) "
                    "ORDER BY memory_id, tag_id",
                    ids,
                ).fetchall(),
                "tripwires": conn.execute(
                    f"SELECT * FROM tripwires WHERE memory_id IN ({marks}) ORDER BY id", ids
                ).fetchall(),
                "embeddings": conn.execute(
                    f"SELECT * FROM memory_embeddings WHERE memory_id IN ({marks}) "
                    "ORDER BY memory_id",
                    ids,
                ).fetchall(),
                "namespace_names": sorted(
                    r[0] for r in conn.execute("SELECT name FROM namespaces").fetchall()
                ),
            }
        finally:
            conn.close()


async def _store(server, ns: str, title: str, content: str, **extra) -> str:
    out = await call(
        server,
        "memory_store",
        {"namespace": ns, "title": title, "content": content, "type": "fact", **extra},
    )
    assert out["ok"], out
    return out["memory"]["id"]


@pytest.fixture
async def brain(tmp_path, monkeypatch) -> Brain:
    db = tmp_path / "scoped.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("MEMORY_NAMESPACE", HIDDEN)
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    server = build_server(transport="http")
    b = Brain(server=server, db_path=str(db))
    with monkeypatch.context() as seeding:
        # An in-process call carries no HTTP request, so under the http
        # transport it is refused; seed as the owner, then drop the override.
        seeding.setattr("gingugu.handlers.fence.request_grant", lambda *_: FULL)
        await _seed(b)
    return b


async def _seed(b: Brain) -> None:
    server = b.server
    # Hidden memories share the visible ones' words - and FakeEmbedder's
    # "alpha" axis - so every lexical and semantic path would rank them.
    shared = "deploy pipeline rollout checklist"
    common = {"type": "pattern", "confidence": "verified", "tags": "rollout"}
    b.ids["A1"] = await _store(server, "alpha", f"alpha {shared}", f"alpha {shared}", **common)
    b.ids["A2"] = await _store(server, "alpha", "alpha second note", f"second {shared}")
    b.ids["G1"] = await _store(server, "gamma", f"gamma {shared}", f"gamma {shared}", **common)
    b.ids["B1"] = await _store(
        server, HIDDEN, f"{MARKER} {shared}", f"alpha {shared} {MARKER} secret", **common
    )
    b.ids["B2"] = await _store(
        server,
        HIDDEN,
        f"{MARKER} fetcher",
        f"alpha {shared} fetcher {MARKER}",
        type="capability",
        metadata={"capability": {"run": f"uv run {MARKER}.py"}},
    )
    b.ids["B3"] = await _store(server, HIDDEN, f"{MARKER} twin", f"{shared} {MARKER} twin")
    for src, dst in (("A1", "B1"), ("B1", "B2"), ("A1", "A2"), ("G1", "B3")):
        out = await call(
            server,
            "memory_relate",
            {"source_id": b.ids[src], "target_id": b.ids[dst], "relation_type": "caused_by"},
        )
        assert out["ok"], out
    out = await call(server, "memory_update", {"memory_id": b.ids["B1"], "pinned": True})
    assert out["ok"], out
    out = await call(
        server,
        "memory_tripwire",
        {"action": "add", "memory_id": b.ids["B1"], "tool": "Bash", "pattern": "deploy"},
    )
    assert out["ok"], out
    b.tripwire_id = out["tripwire"]["id"]


def scope_to(monkeypatch, grant: Grant = GRANT) -> None:
    """Make every later tool call arrive as ``grant``'s token."""
    monkeypatch.setattr("gingugu.handlers.fence.request_grant", lambda *_: grant)
