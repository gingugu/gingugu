"""Capability pointers, tool surface: what store, update and the reads return.

The pure functions underneath are pinned in ``test_capability.py``. This file
holds the contract a client sees: a ``capability`` memory is refused without a
``run``, every read shows ``run``/``path``/``exists`` (compact reads too - the
run line is the whole point), and ``memory_recall`` carries a separate
``capabilities`` section so a pointer is never buried under the procedure
memories it replaces.
"""

from __future__ import annotations

import json

import pytest

from tests.test_embeddings import FakeEmbedder


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


async def _call(server, tool: str, args: dict) -> dict:
    return _payload(await server.call_tool(tool, args))


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "cap.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "cap")
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    return build_server()


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "cap-http.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "cap")
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.grants import FULL
    from gingugu.server import build_server

    # In-process calls carry no HTTP request; call as the owner.
    monkeypatch.setattr("gingugu.handlers.fence.request_grant", lambda *_: FULL)
    return build_server(transport="http")


def _cap_args(title="alpha fetcher", run="uv run fetch.py KEY", path=None, **extra) -> dict:
    block = {"run": run}
    if path is not None:
        block["path"] = path
    return {
        "title": title,
        "content": "Fetches an alpha record.",
        "type": "capability",
        "metadata": {"capability": block},
        **extra,
    }


# --- write validation ------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_accepts_a_capability_and_echoes_the_block(server, tmp_path):
    script = tmp_path / "fetch.py"
    script.write_text("")
    out = await _call(server, "memory_store", _cap_args(path=str(script)))
    assert out["ok"] is True
    assert out["memory"]["type"] == "capability"
    assert out["memory"]["capability"] == {
        "run": "uv run fetch.py KEY",
        "path": str(script),
        "exists": True,
    }


@pytest.mark.asyncio
async def test_store_refuses_a_capability_without_run(server):
    args = _cap_args()
    args["metadata"] = {"capability": {"path": "x.py"}}
    out = await _call(server, "memory_store", args)
    assert out["ok"] is False
    assert "run is required" in out["error"]


@pytest.mark.asyncio
async def test_store_refuses_a_capability_with_no_metadata(server):
    args = _cap_args()
    del args["metadata"]
    out = await _call(server, "memory_store", args)
    assert out["ok"] is False
    assert "needs metadata.capability" in out["error"]


@pytest.mark.asyncio
async def test_store_refuses_the_reserved_key_on_another_type(server):
    args = _cap_args()
    args["type"] = "workflow"
    out = await _call(server, "memory_store", args)
    assert out["ok"] is False
    assert "reserved" in out["error"]


@pytest.mark.asyncio
async def test_metadata_as_a_json_string_works_too(server):
    args = _cap_args()
    args["metadata"] = json.dumps(args["metadata"])
    out = await _call(server, "memory_store", args)
    assert out["ok"] is True
    assert out["memory"]["capability"]["run"] == "uv run fetch.py KEY"


@pytest.mark.asyncio
async def test_update_validates_the_final_state_not_the_patch(server):
    stored = await _call(server, "memory_store", _cap_args())
    mid = stored["memory"]["id"]

    # Touching an unrelated field keeps the existing valid block: allowed.
    ok = await _call(server, "memory_update", {"memory_id": mid, "title": "alpha getter"})
    assert ok["ok"] is True
    assert ok["memory"]["capability"]["run"] == "uv run fetch.py KEY"

    # Clearing metadata on a capability leaves an unfollowable pointer: refused.
    cleared = await _call(server, "memory_update", {"memory_id": mid, "metadata": ""})
    assert cleared["ok"] is False

    # Retyping away while the block remains: refused.
    retyped = await _call(server, "memory_update", {"memory_id": mid, "type": "workflow"})
    assert retyped["ok"] is False

    # Retyping away AND clearing the block together: allowed.
    both = await _call(
        server, "memory_update", {"memory_id": mid, "type": "workflow", "metadata": ""}
    )
    assert both["ok"] is True
    assert "capability" not in both["memory"]


@pytest.mark.asyncio
async def test_update_can_promote_a_workflow_into_a_capability(server):
    stored = await _call(
        server,
        "memory_store",
        {"title": "alpha by hand", "content": "curl ...", "type": "workflow"},
    )
    mid = stored["memory"]["id"]
    refused = await _call(server, "memory_update", {"memory_id": mid, "type": "capability"})
    assert refused["ok"] is False
    promoted = await _call(
        server,
        "memory_update",
        {"memory_id": mid, "type": "capability", "metadata": {"capability": {"run": "alpha"}}},
    )
    assert promoted["ok"] is True
    assert promoted["memory"]["capability"] == {"run": "alpha"}


@pytest.mark.asyncio
async def test_a_failed_update_changes_nothing(server):
    stored = await _call(server, "memory_store", _cap_args())
    mid = stored["memory"]["id"]
    await _call(server, "memory_update", {"memory_id": mid, "metadata": "", "title": "renamed"})
    found = await _call(server, "memory_search", {"ids": mid})
    assert found["memories"][0]["title"] == "alpha fetcher"
    assert found["memories"][0]["capability"]["run"] == "uv run fetch.py KEY"


@pytest.mark.asyncio
async def test_update_on_a_missing_memory_still_says_not_found(server):
    out = await _call(server, "memory_update", {"memory_id": "nope", "title": "x"})
    assert out["ok"] is False
    assert "not found" in out["error"]


# --- the read side ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [False, True])
async def test_every_read_surface_shows_the_block(server, tmp_path, compact):
    gone = str(tmp_path / "deleted.py")
    stored = await _call(server, "memory_store", _cap_args(path=gone))
    mid = stored["memory"]["id"]
    expected = {"run": "uv run fetch.py KEY", "path": gone, "exists": False}

    recall = await _call(server, "memory_recall", {"query": "alpha", "compact": compact})
    search = await _call(server, "memory_search", {"ids": mid, "compact": compact})
    context = await _call(server, "memory_context", {"task_hint": "alpha", "compact": compact})

    for payload in (recall, search, context):
        hit = next(m for m in payload["memories"] if m["id"] == mid)
        assert hit["capability"] == expected


@pytest.mark.asyncio
async def test_ordinary_memories_carry_no_capability_field(server):
    await _call(server, "memory_store", {"title": "alpha", "content": "c", "type": "fact"})
    recall = await _call(server, "memory_recall", {"query": "alpha"})
    assert all("capability" not in m for m in recall["memories"])


@pytest.mark.asyncio
async def test_relative_paths_resolve_against_the_namespace_repo(server, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "fetch.py").write_text("")
    await _call(server, "memory_namespaces", {"action": "create", "name": "cap"})
    await _call(
        server, "memory_namespaces", {"action": "update", "name": "cap", "path": str(tmp_path)}
    )
    stored = await _call(server, "memory_store", _cap_args(path="scripts/fetch.py"))
    assert stored["memory"]["capability"]["exists"] is True


@pytest.mark.asyncio
async def test_http_transport_never_reports_exists(http_server, tmp_path):
    script = tmp_path / "fetch.py"
    script.write_text("")
    stored = await _call(http_server, "memory_store", _cap_args(path=str(script)))
    assert stored["memory"]["capability"]["exists"] is None


# --- the recall lane ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_surfaces_a_capability_the_main_ranking_missed(server):
    stored = await _call(
        server,
        "memory_store",
        _cap_args(title="the fetcher", run="fetch-run") | {"content": "alpha"},
    )
    cap_id = stored["memory"]["id"]
    for i in range(4):
        await _call(
            server,
            "memory_store",
            {"title": f"alpha manual {i}", "content": "alpha alpha alpha", "type": "workflow"},
        )
    out = await _call(server, "memory_recall", {"query": "alpha", "limit": 2})
    assert cap_id not in {m["id"] for m in out["memories"]}
    lane = out["capabilities"]
    assert [c["id"] for c in lane] == [cap_id]
    assert lane[0]["capability"] == {"run": "fetch-run"}
    assert lane[0]["similarity"] == pytest.approx(1.0)
    assert lane[0]["namespace"] == "cap"
    assert "summary" in lane[0] and "content" not in lane[0]  # always compact


@pytest.mark.asyncio
async def test_a_capability_already_in_the_results_is_not_repeated(server):
    stored = await _call(server, "memory_store", _cap_args() | {"content": "alpha"})
    out = await _call(server, "memory_recall", {"query": "alpha"})
    assert stored["memory"]["id"] in {m["id"] for m in out["memories"]}
    assert "capabilities" not in out


@pytest.mark.asyncio
async def test_no_lane_when_nothing_clears_the_bar(server):
    await _call(server, "memory_store", _cap_args(title="delta", run="d") | {"content": "delta"})
    await _call(server, "memory_store", {"title": "alpha", "content": "alpha", "type": "fact"})
    out = await _call(server, "memory_recall", {"query": "alpha"})
    assert "capabilities" not in out


@pytest.mark.asyncio
async def test_no_lane_when_the_caller_filtered_by_type(server):
    await _call(server, "memory_store", _cap_args(title="the fetcher") | {"content": "alpha"})
    for i in range(3):
        await _call(
            server,
            "memory_store",
            {"title": f"alpha manual {i}", "content": "alpha alpha", "type": "workflow"},
        )
    out = await _call(server, "memory_recall", {"query": "alpha", "type": "workflow"})
    assert "capabilities" not in out


@pytest.mark.asyncio
async def test_the_lane_is_not_credited_as_an_access(server):
    stored = await _call(
        server, "memory_store", _cap_args(title="the fetcher") | {"content": "alpha"}
    )
    cap_id = stored["memory"]["id"]
    for i in range(4):
        await _call(
            server,
            "memory_store",
            {"title": f"alpha manual {i}", "content": "alpha alpha alpha", "type": "workflow"},
        )
    out = await _call(server, "memory_recall", {"query": "alpha", "limit": 2})
    assert out["capabilities"][0]["id"] == cap_id
    after = await _call(server, "memory_search", {"ids": cap_id})
    assert after["memories"][0]["access_count"] == 0


# --- the involuntary-recall gate -----------------------------------------------------


def test_capabilities_are_actionable_for_the_prompt_gate():
    from gingugu.recall_gate import ACTIONABLE_TYPES

    assert "capability" in ACTIONABLE_TYPES
