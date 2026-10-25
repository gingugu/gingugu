"""``gingugu serve``'s ``/ui/api/*`` routes: the Explorer API on a remote brain.

GET-only, owner tokens only (like the hook routes), answered by the same
``ui_api.dispatch`` the local process uses, over the brain's own DB.
"""

from __future__ import annotations

import httpx2
import pytest

from gingugu.grants import READ
from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import SERVE_TOKEN, Served, free_port, make_brain
from tests.ui_api_fixtures import seed, snapshot_signals


@pytest.fixture
def brain(tmp_path, monkeypatch):
    b = make_brain(tmp_path, monkeypatch, credentials=False)
    b.seeded = seed(tmp_path, monkeypatch)
    return b


@pytest.fixture
async def served(brain):
    s = Served(free_port())
    await s.start(brain.app())
    try:
        yield s
    finally:
        await s.stop()


async def _get(served, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(timeout=10) as client:
        return await client.get(served.url + path, headers=headers, **kw)


async def test_graph_needs_a_token(served):
    assert (await _get(served, "/ui/api/graph")).status_code == 401


async def test_owner_token_reads_the_graph(served):
    response = await _get(served, "/ui/api/graph", SERVE_TOKEN)
    assert response.status_code == 200
    assert len(response.json()["nodes"]) == 5


async def test_machine_owner_token_reads_the_graph(served, brain):
    response = await _get(served, "/ui/api/graph", brain.machine)
    assert response.status_code == 200


async def test_scoped_token_is_refused(served, brain):
    scoped = TokenStore(brain.tmp_path / "serve_tokens.json").add("viewer", {"crow": READ})
    response = await _get(served, "/ui/api/graph", scoped)
    assert response.status_code == 403
    assert response.json()["ok"] is False


async def test_detail_and_404(served, brain):
    rule = brain.seeded.ids["rule"]
    ok = await _get(served, f"/ui/api/memory/{rule}", SERVE_TOKEN)
    assert ok.status_code == 200 and ok.json()["memory"]["id"] == rule
    missing = await _get(served, "/ui/api/memory/nope", SERVE_TOKEN)
    assert missing.status_code == 404


async def test_query_params_reach_dispatch(served):
    response = await _get(served, "/ui/api/stats", SERVE_TOKEN, params={"ns": "crow"})
    assert response.status_code == 200
    assert response.json()["stats"]["total_memories"] == 2


async def test_writes_are_not_routed(served):
    async with httpx2.AsyncClient(timeout=10) as client:
        response = await client.post(
            served.url + "/ui/api/graph", headers={"Authorization": f"Bearer {SERVE_TOKEN}"}
        )
    assert response.status_code == 405


async def test_remote_search_is_an_observer(served, brain):
    before = snapshot_signals(brain.seeded.db_path)
    response = await _get(served, "/ui/api/search", SERVE_TOKEN, params={"q": "stack parent"})
    assert response.status_code == 200 and response.json()["hits"]
    assert snapshot_signals(brain.seeded.db_path) == before


async def test_derived_token_is_refused(served, brain):
    from gingugu.grants import FULL

    derived = brain.derived.mint(FULL, 60)
    response = await _get(served, "/ui/api/graph", derived)
    assert response.status_code == 403
