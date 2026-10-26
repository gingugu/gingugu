"""The brain's side of board mail: ``/hook/inbox`` on ``gingugu serve``.

Owner tokens only, like every hook route. The persona and the namespaces the
client's grant can read arrive with the request (``null`` = unfenced), as do
the ids already shown this session. Read-only: nothing is marked read.
"""

from __future__ import annotations

import sqlite3

import httpx2
import pytest

from gingugu import board
from gingugu.database import Database
from gingugu.grants import Grant
from gingugu.namespaces import NamespaceManager
from tests.proxy_fixtures import Served, free_port, make_brain


@pytest.fixture
def brain(tmp_path, monkeypatch):
    b = make_brain(tmp_path, monkeypatch, credentials=False)
    database = Database(tmp_path / "brain.db")
    conn = database.connect()
    from gingugu.config import load_config

    nsm = NamespaceManager(conn, load_config())
    ids = {name: nsm.get_or_create(name).id for name in ("crow", "tyrone")}
    b.crow_post = board.post(
        conn, namespace_id=ids["crow"], author="tyrone", recipient="beepboop", body="in crow"
    )["id"]
    b.tyrone_post = board.post(
        conn, namespace_id=ids["tyrone"], author="tyrone", recipient="beepboop", body="in tyrone"
    )["id"]
    board.post(conn, namespace_id=ids["crow"], author="tyrone", recipient="gemini", body="not mine")
    conn.commit()
    database.close()
    return b


@pytest.fixture
async def served(brain):
    s = Served(free_port())
    await s.start(brain.app())
    try:
        yield s
    finally:
        await s.stop()


async def _inbox(served, body, token):
    async with httpx2.AsyncClient(timeout=10) as client:
        return await client.post(
            served.url + "/hook/inbox", json=body, headers={"Authorization": f"Bearer {token}"}
        )


def _body(**over):
    body = {"persona": "beepboop", "namespaces": None, "shown": []}
    body.update(over)
    return body


async def test_unfenced_inbox_spans_every_namespace(brain, served):
    r = await _inbox(served, _body(), brain.machine)
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["posts"]]
    assert ids == [brain.crow_post, brain.tyrone_post]


async def test_the_namespaces_sent_fence_the_inbox(brain, served):
    r = await _inbox(served, _body(namespaces=["crow"]), brain.machine)
    assert [p["id"] for p in r.json()["posts"]] == [brain.crow_post]


async def test_shown_posts_are_left_out(brain, served):
    r = await _inbox(served, _body(shown=[brain.crow_post]), brain.machine)
    assert [p["id"] for p in r.json()["posts"]] == [brain.tyrone_post]


async def test_the_inbox_is_never_marked_read(brain, served):
    await _inbox(served, _body(), brain.machine)
    conn = sqlite3.connect(brain.tmp_path / "brain.db")
    try:
        assert conn.execute("SELECT count(*) FROM board_reads").fetchone()[0] == 0
    finally:
        conn.close()


async def test_only_an_owner_token_may_ask(brain, served):
    derived = brain.derived.mint(Grant("derived:x", {"crow": "write"}, derived=True), 60)
    r = await _inbox(served, _body(), derived)
    assert r.status_code == 403


@pytest.mark.parametrize(
    "over",
    [
        {"persona": None},
        {"persona": "not a persona!"},
        {"persona": "*"},
        {"namespaces": "crow"},
        {"namespaces": ["../etc"]},
        {"shown": "p1"},
        {"shown": [1]},
    ],
)
async def test_a_malformed_body_is_refused(brain, served, over):
    r = await _inbox(served, _body(**over), brain.machine)
    assert r.status_code == 400
