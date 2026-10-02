"""``MEMORY_GRANT``: a stdio server fenced to a grant, for warm minions.

A subagent's own stdio server is started with a grant in its environment, so
the fence is the server's - the same chokepoints a scoped serve token hits -
rather than an instruction the minion is trusted to follow. Unset, stdio is the
full grant exactly as before. A spec that cannot be honoured stops the server
from starting at all: a typo must never fall back to the whole brain.
"""

from __future__ import annotations

import pytest

from gingugu.config import load_config
from gingugu.grants import FULL, READ, WRITE
from gingugu.handlers.fence import stdio_grant
from tests.scoped_fixtures import HIDDEN, MARKER, FakeEmbedder, call

SPEC = "alpha=write,gamma=read"


def test_config_reads_memory_grant(monkeypatch):
    monkeypatch.setenv("MEMORY_GRANT", SPEC)
    assert load_config().grant == SPEC
    monkeypatch.setenv("MEMORY_GRANT", "")
    assert load_config().grant == ""  # set-but-blank stays visible to the fence
    monkeypatch.delenv("MEMORY_GRANT")
    assert load_config().grant is None


def test_no_grant_is_the_full_grant():
    assert stdio_grant(None) is FULL


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_set_but_blank_grant_refuses(blank):
    # MEMORY_GRANT: "" in an agent file is a mistake, not a request for the
    # whole brain: fail closed like any other spec that cannot be honoured.
    with pytest.raises(ValueError):
        stdio_grant(blank)


def test_a_spec_becomes_a_scoped_grant():
    grant = stdio_grant(SPEC)
    assert not grant.is_full
    assert grant.level("alpha") == WRITE
    assert grant.level("gamma") == READ
    assert grant.level(HIDDEN) is None


@pytest.mark.parametrize(
    "bad", ["*=write", "alpha", "alpha=admin", "alpha=read,alpha=write", ",", "=read"]
)
def test_a_bad_or_owner_equivalent_spec_refuses(bad):
    with pytest.raises(ValueError):
        stdio_grant(bad)


def _build(tmp_path, monkeypatch, spec: str | None):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "stdio.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", HIDDEN)
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    if spec is None:
        monkeypatch.delenv("MEMORY_GRANT", raising=False)
    else:
        monkeypatch.setenv("MEMORY_GRANT", spec)
    from gingugu.server import build_server

    return build_server(transport="stdio")


@pytest.mark.parametrize("bad", ["*=write", ""])
def test_a_bad_spec_stops_the_server_from_starting(tmp_path, monkeypatch, bad):
    with pytest.raises(ValueError):
        _build(tmp_path, monkeypatch, bad)


async def _seed(tmp_path, monkeypatch) -> dict[str, str]:
    owner = _build(tmp_path, monkeypatch, None)
    ids = {}
    for key, ns, text in (
        ("A1", "alpha", "alpha rollout checklist"),
        ("G1", "gamma", "gamma rollout checklist"),
        ("B1", HIDDEN, f"{MARKER} rollout checklist secret"),
    ):
        out = await call(
            owner, "memory_store", {"namespace": ns, "title": text, "content": text, "type": "fact"}
        )
        assert out["ok"], out
        ids[key] = out["memory"]["id"]
    return ids


async def test_stdio_under_a_grant_is_fenced(tmp_path, monkeypatch):
    ids = await _seed(tmp_path, monkeypatch)
    minion = _build(tmp_path, monkeypatch, SPEC)

    # Reads stop at the fence: the hidden namespace does not exist for it.
    found = await call(minion, "memory_recall", {"query": "rollout checklist"})
    assert found["ok"], found
    seen = {m["id"] for m in found["memories"]}
    assert ids["B1"] not in seen
    assert MARKER not in str(found)
    hidden = await call(minion, "memory_search", {"ids": ids["B1"]})
    assert not hidden.get("memories")

    # Writes land only where the grant says write.
    ok = await call(
        minion, "memory_store", {"namespace": "alpha", "title": "t", "content": "c", "type": "fact"}
    )
    assert ok["ok"], ok
    ro = await call(
        minion, "memory_store", {"namespace": "gamma", "title": "t", "content": "c", "type": "fact"}
    )
    assert ro["ok"] is False
    gone = await call(minion, "memory_forget", {"memory_id": ids["B1"], "hard_delete": True})
    assert gone["ok"] is False

    # Whole-brain tools are closed, as for a scoped token.
    export = await call(minion, "memory_export", {})
    assert export["ok"] is False and "scoped" in export["error"]


async def test_a_scratch_namespace_is_created_on_first_write(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    minion = _build(tmp_path, monkeypatch, "alpha=read,minions=write")
    out = await call(
        minion,
        "memory_store",
        {"namespace": "minions", "title": "finding", "content": "c", "type": "fact"},
    )
    assert out["ok"], out


async def test_stdio_without_a_grant_is_unchanged(tmp_path, monkeypatch):
    ids = await _seed(tmp_path, monkeypatch)
    owner = _build(tmp_path, monkeypatch, None)
    out = await call(owner, "memory_search", {"ids": ids["B1"]})
    assert out["memories"][0]["id"] == ids["B1"]
