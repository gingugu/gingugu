"""The canary: a scoped token can neither see nor change what its grant omits.

Every tool, with the options that reach across namespaces, is called as a
token granted ``alpha`` (write), ``gamma`` (read) and ``scratch`` (write).
The hidden namespace - also the server's configured default - must not leak
through any field of any response: not its marker text, not its memory ids
unless the caller supplied them, not its name unless the caller typed it.
And every row it owns must be byte-identical afterwards, access clocks
included, so a write or a touch that never surfaced is caught too.

The calls are a list rather than one test each so a new tool is one line,
and so the snapshot comparison covers the whole sequence at once.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tests.scoped_fixtures import HIDDEN, MARKER, call, scope_to


def _calls(ids: dict[str, str], tripwire_id: str) -> list[tuple[str, dict]]:
    a1, a2, g1 = ids["A1"], ids["A2"], ids["G1"]
    b1, b2, b3 = ids["B1"], ids["B2"], ids["B3"]
    q = "alpha deploy pipeline rollout"
    return [
        # --- reads that default, widen, spread or pull in neighbours ---
        ("memory_recall", {"query": q}),
        ("memory_recall", {"query": q, "include_related": True}),
        ("memory_recall", {"query": q, "namespace": "alpha", "include_related": True}),
        ("memory_recall", {"query": q, "namespace": "gamma", "include_related": True}),
        ("memory_recall", {"query": MARKER}),
        ("memory_recall", {"query": "zzqq nothing matches this", "namespace": "alpha"}),
        ("memory_recall", {"query": q, "namespace": HIDDEN}),
        ("memory_recall", {"query": q, "namespace": f"alpha,{HIDDEN}"}),
        ("memory_recall", {"query": "fetcher alpha", "compact": True}),
        ("memory_search", {}),
        ("memory_search", {"query": MARKER}),
        ("memory_search", {"query": "zzqq nothing", "namespace": "alpha"}),
        ("memory_search", {"ids": f"{a1},{b1},{b2}"}),
        ("memory_search", {"orphans": True, "include_deprecated": True}),
        ("memory_search", {"pinned": True}),
        ("memory_search", {"tags": "rollout"}),
        ("memory_search", {"type": "capability"}),
        ("memory_search", {"sort_by": "accessed", "limit": 50}),
        ("memory_context", {}),
        ("memory_context", {"task_hint": q}),
        ("memory_context", {"namespace": "alpha", "task_hint": q, "explain": True}),
        ("memory_context", {"namespace": "gamma", "compact": True}),
        ("memory_context", {"namespace": f"alpha,{HIDDEN}"}),
        ("memory_stats", {}),
        ("memory_stats", {"namespace": "alpha", "review_limit": 100}),
        ("memory_stats", {"namespace": HIDDEN}),
        ("memory_stats", {"namespace": "alpha,gamma"}),
        ("memory_edges", {}),
        ("memory_edges", {"memory_id": a1}),
        ("memory_edges", {"memory_id": g1}),
        ("memory_edges", {"memory_id": b1}),
        ("memory_edges", {"namespace": HIDDEN}),
        ("memory_edges", {"namespace": "alpha"}),
        ("memory_excerpt", {"memory_id": b1}),
        ("memory_namespaces", {}),
        ("memory_namespaces", {"action": "list"}),
        ("memory_tripwire", {"action": "list"}),
        ("memory_tripwire", {"action": "list", "memory_id": b1}),
        ("memory_tripwire", {"action": "test", "tool": "Bash", "input": '{"command": "deploy"}'}),
        (
            "memory_tripwire",
            {
                "action": "test",
                "tool": "Bash",
                "input": '{"command": "deploy"}',
                "namespace": HIDDEN,
            },
        ),
        ("memory_consolidate", {}),
        ("memory_consolidate", {"namespace": "alpha"}),
        ("memory_consolidate", {"namespace": HIDDEN}),
        # --- writes aimed at the hidden namespace, or across into it ---
        ("memory_store", {"title": "no ns", "content": "omitted", "type": "fact"}),
        ("memory_store", {"namespace": HIDDEN, "title": "x", "content": "y", "type": "fact"}),
        (
            "memory_store",
            {"namespace": "not-granted", "title": "x", "content": "y", "type": "fact"},
        ),
        ("memory_update", {"memory_id": b1, "content": "overwritten"}),
        ("memory_update", {"memory_id": b1, "pinned": False}),
        ("memory_update", {"memory_id": b1, "resolve_claims": "all"}),
        ("memory_update", {"memory_id": b1, "tags": "pwned"}),
        ("memory_forget", {"memory_id": b1}),
        ("memory_forget", {"memory_id": b2, "hard_delete": True}),
        ("memory_relate", {"source_id": a1, "target_id": b1, "relation_type": "supersedes"}),
        ("memory_relate", {"source_id": b3, "target_id": a2, "relation_type": "caused_by"}),
        (
            "memory_relate",
            {"edges": [{"source_id": a1, "target_id": b3, "relation_type": "caused_by"}]},
        ),
        ("memory_unrelate", {"source_id": a1, "target_id": b1}),
        ("memory_unrelate", {"source_id": b1, "target_id": b2, "relation_type": "caused_by"}),
        (
            "memory_unrelate",
            {"source_id": a1, "target_id": b1, "new_type": "related_to", "old_type": "caused_by"},
        ),
        ("memory_unrelate", {"source_id": g1, "target_id": b3, "dry_run": False}),
        ("memory_consolidate", {"memory_ids": f"{b1},{b3}", "title": "m", "content": "c"}),
        ("memory_consolidate", {"memory_ids": f"{a1},{b1}", "title": "m", "content": "c"}),
        ("memory_tripwire", {"action": "add", "memory_id": b1, "tool": "Bash", "pattern": "x"}),
        ("memory_tripwire", {"action": "remove", "tripwire_id": tripwire_id}),
        # --- whole-brain tools: closed to any scoped token ---
        ("memory_namespaces", {"action": "create", "name": "evil"}),
        ("memory_namespaces", {"action": "delete", "name": HIDDEN, "cascade": True}),
        ("memory_namespaces", {"action": "update", "name": HIDDEN, "description": "x"}),
        ("memory_export", {}),
        ("memory_export", {"namespace": "alpha"}),
        ("memory_import", {"data": {"memories": []}}),
        ("memory_dream", {"action": "list"}),
        ("memory_dream", {"action": "run"}),
        ("credential_list", {}),
        ("credential_store", {"service_name": "s", "fields": '{"k": "v"}'}),
        ("credential_get", {"service_name": "s", "reveal": True}),
    ]


async def test_hidden_namespace_never_leaks_or_changes(brain, monkeypatch):
    before = brain.snapshot()
    scope_to(monkeypatch)
    hidden_ids = brain.hidden_ids() + [brain.tripwire_id]
    failures: list[str] = []
    for tool, args in _calls(brain.ids, brain.tripwire_id):
        try:
            result = await call(brain.server, tool, args)
        except ToolError as exc:  # the canary's own args are wrong - fix them
            failures.append(f"{tool} {json.dumps(args)}: invalid call: {exc}")
            continue
        text = json.dumps(result)
        asked = json.dumps(args)
        if MARKER in text:
            failures.append(f"{tool} {asked}: marker leaked")
        for hid in hidden_ids:
            if hid in text and hid not in asked:
                failures.append(f"{tool} {asked}: hidden id {hid} leaked")
        if HIDDEN in text and HIDDEN not in asked:
            failures.append(f"{tool} {asked}: hidden namespace name leaked")
    assert not failures, "\n".join(failures)
    after = brain.snapshot()
    for key in before:
        assert after[key] == before[key], f"hidden {key} changed under a scoped token"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("memory_excerpt", {"memory_id": "B1"}),
        ("memory_forget", {"memory_id": "B1"}),
        ("memory_update", {"memory_id": "B1", "content": "x"}),
    ],
)
async def test_hidden_id_reads_exactly_like_an_unknown_one(brain, monkeypatch, tool, args):
    scope_to(monkeypatch)
    hidden = await call(brain.server, tool, {**args, "memory_id": brain.ids["B1"]})
    unknown = await call(brain.server, tool, {**args, "memory_id": "no-such-id"})
    assert hidden["ok"] is False and unknown["ok"] is False
    assert hidden["error"].replace(brain.ids["B1"], "ID") == unknown["error"].replace(
        "no-such-id", "ID"
    )


async def test_hidden_namespace_reads_exactly_like_an_unknown_one(brain, monkeypatch):
    scope_to(monkeypatch)
    for tool, args in (("memory_recall", {"query": "x"}), ("memory_stats", {})):
        hidden = await call(brain.server, tool, {**args, "namespace": HIDDEN})
        unknown = await call(brain.server, tool, {**args, "namespace": "never-made"})
        assert hidden["error"].replace(HIDDEN, "NS") == unknown["error"].replace("never-made", "NS")
