"""Tests for multi-namespace ``memory_stats``: one call at session start.

The startup protocol made two ``memory_stats`` calls, one per namespace, and
each response repeated the same namespace-independent payload - the namespace
inventory, ``access_log_rows`` and the ``credentials`` block - byte for byte.
A comma-separated ``namespace`` now returns that global block once, plus each
namespace's own scoped stats. Single-namespace and unscoped calls keep their
exact shape, so no existing caller loses a field.
"""

from __future__ import annotations

import json
import re

import pytest

GLOBAL_KEYS = {"namespaces", "access_log_rows", "query_log_rows", "credentials"}


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "stats.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server()


async def _stats(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_stats", kwargs))


async def _seed(server) -> None:
    for ns, n in (("crow", 2), ("gingugu", 3)):
        for i in range(n):
            await server.call_tool(
                "memory_store",
                {
                    "title": f"{ns} {i}",
                    "content": f"body {ns} {i}",
                    "type": "fact",
                    "namespace": ns,
                },
            )


@pytest.mark.asyncio
async def test_multi_namespace_returns_the_global_block_once(server) -> None:
    await _seed(server)
    out = await _stats(server, namespace="crow,gingugu")

    assert out["ok"] is True
    assert out["namespaces"] == ["crow", "gingugu"]
    assert set(out["global"]) == GLOBAL_KEYS
    assert set(out["by_namespace"]) == {"crow", "gingugu"}
    for block in out["by_namespace"].values():
        assert GLOBAL_KEYS.isdisjoint(block)


@pytest.mark.asyncio
async def test_each_namespace_block_matches_its_single_namespace_call(server) -> None:
    """The multi call is the single calls deduplicated - not a new computation
    with its own drift."""
    await _seed(server)
    multi = await _stats(server, namespace="crow,gingugu")

    for ns in ("crow", "gingugu"):
        single = (await _stats(server, namespace=ns))["stats"]
        expected = {k: v for k, v in single.items() if k not in GLOBAL_KEYS}
        assert multi["by_namespace"][ns] == expected
    assert multi["by_namespace"]["crow"]["total_memories"] == 2
    assert multi["by_namespace"]["gingugu"]["total_memories"] == 3


@pytest.mark.asyncio
async def test_global_block_matches_the_unscoped_call(server) -> None:
    await _seed(server)
    multi = await _stats(server, namespace="crow,gingugu")
    unscoped = (await _stats(server))["stats"]
    assert multi["global"] == {k: unscoped[k] for k in GLOBAL_KEYS}


@pytest.mark.asyncio
async def test_single_namespace_call_keeps_its_shape(server) -> None:
    """Nothing is removed from any response shape a caller already has."""
    await _seed(server)
    out = await _stats(server, namespace="crow")
    assert out["ok"] is True
    assert GLOBAL_KEYS <= set(out["stats"])
    assert "by_namespace" not in out


@pytest.mark.asyncio
async def test_unknown_namespace_in_the_list_fails_the_call_and_names_it(server) -> None:
    await _seed(server)
    out = await _stats(server, namespace="crow,nope")
    assert out["ok"] is False
    assert "nope" in out["error"]


@pytest.mark.asyncio
async def test_duplicate_and_padded_names_are_normalised(server) -> None:
    await _seed(server)
    out = await _stats(server, namespace=" crow , gingugu,crow")
    assert out["ok"] is True
    assert out["namespaces"] == ["crow", "gingugu"]


@pytest.mark.asyncio
async def test_review_limit_applies_to_every_namespace_block(server) -> None:
    await _seed(server)
    out = await _stats(server, namespace="crow,gingugu", review_limit=1)
    for block in out["by_namespace"].values():
        assert len(block["graph"]["orphan_sample"]) <= 1


def test_startup_protocol_asks_for_one_stats_call() -> None:
    """Every place that teaches the session-start contract asks for a single
    comma-separated ``memory_stats`` call, not one per namespace."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    templates = root / "src" / "gingugu" / "bootstrap" / "templates"
    for path in (
        templates / "rules_protocol.md.tmpl",
        templates / "session_start.py.tmpl",
        root / ".claude" / "hooks" / "session_start.py",
        root / "CLAUDE.md",
        root / "AGENTS.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert 'memory_stats(namespace="crow")' not in text, path
        # "crow,<project>" or "crow[,<persona>],<project>": one call, a list.
        assert re.search(r'memory_stats\(namespace="crow(,|\[,)', text), path


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["crow,crow", " crow ", "crow,"])
async def test_a_list_that_normalises_to_one_name_is_a_single_namespace_call(server, raw) -> None:
    """Normalisation happens before the shape is chosen: a list that collapses
    to one real name is that name's single-namespace call, not a lookup of the
    raw string."""
    await _seed(server)
    out = await _stats(server, namespace=raw)
    assert out["ok"] is True
    assert out["stats"]["total_memories"] == 2
