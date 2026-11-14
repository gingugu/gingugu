"""A crew mate's scoped token on the brain: derive no wider than itself, read its own mail.

A crew mate runs its own local brain and reaches a shared one only through a
scoped token (e.g. ``crew=write``). Its proxy trades that token at
``/token/derive`` like an owner's would, so the scoped token may derive - but
only a token no wider than itself, named for itself (the board author), with
no machine origin, and only a few at a time so it cannot fill the brain's cap
and lock the owners out. Its board hook may read ``/hook/inbox`` for its own
persona, fenced to its grant. Every other hook route stays owner-only.
"""

from __future__ import annotations

import httpx2
import pytest
from starlette.testclient import TestClient

from gingugu import board
from gingugu.database import Database
from gingugu.derived_tokens import DerivedTokens
from gingugu.namespaces import NamespaceManager
from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import Served, free_port, make_brain
from tests.test_embeddings import FakeEmbedder

SERVE_TOKEN = "serve-owner-" + "s" * 32
DERIVE = "/token/derive"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "brain.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "server-default")
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.serve import build_app
    from gingugu.server import build_server

    tokens = TokenStore(tmp_path / "serve_tokens.json")
    derived = DerivedTokens()
    app = build_app(build_server(transport="http"), SERVE_TOKEN, tokens, derived)

    class Rig:
        pass

    r = Rig()
    r.app, r.derived = app, derived
    r.mate = tokens.add("polly", {"crew": "write", "lore": "read"})
    r.machine = tokens.add_owner("macbookpro")
    return r


def _derive(client, token, body) -> httpx2.Response:
    return client.post(DERIVE, headers={"Authorization": f"Bearer {token}"}, json=body)


# --- derive ------------------------------------------------------------------


def test_scoped_token_derives_its_own_grant_by_default(rig):
    with TestClient(rig.app) as client:
        resp = _derive(client, rig.mate, {"home": "crew"})
    assert resp.status_code == 200, resp.text
    grant = rig.derived.resolve(resp.json()["token"])
    assert grant.derived and grant.home == "crew"
    assert grant.namespaces == {"crew": "write", "lore": "read"}
    assert grant.origin is None  # a scoped token names no machine


def test_scoped_derive_is_named_for_the_token_not_the_body(rig):
    with TestClient(rig.app) as client:
        resp = _derive(client, rig.mate, {"home": "crew", "name": "beepboop"})
    assert resp.status_code == 200, resp.text
    assert rig.derived.resolve(resp.json()["token"]).name == "derived:polly"


def test_scoped_derive_may_narrow(rig):
    with TestClient(rig.app) as client:
        resp = _derive(client, rig.mate, {"grant": "crew=read", "home": "crew"})
    assert resp.status_code == 200, resp.text
    grant = rig.derived.resolve(resp.json()["token"])
    assert grant.namespaces == {"crew": "read"}


@pytest.mark.parametrize(
    "spec",
    ["crow=read", "crew=write,crow=read", "lore=write", "*=read", "*=write"],
)
def test_scoped_derive_may_never_widen(rig, spec):
    with TestClient(rig.app) as client:
        resp = _derive(client, rig.mate, {"grant": spec, "home": "crew"})
    assert resp.status_code == 403, resp.text
    assert rig.derived.live() == 0


def test_scoped_derive_home_must_be_readable(rig):
    with TestClient(rig.app) as client:
        resp = _derive(client, rig.mate, {"home": "crow"})
    assert resp.status_code == 400, resp.text


def test_scoped_token_cannot_fill_the_cap_and_lock_out_owners(rig):
    from gingugu.serve_derive import SCOPED_LIVE_MAX

    with TestClient(rig.app) as client:
        for _ in range(SCOPED_LIVE_MAX):
            assert _derive(client, rig.mate, {"home": "crew"}).status_code == 200
        assert _derive(client, rig.mate, {"home": "crew"}).status_code == 429
        assert _derive(client, rig.machine, {"home": "gingugu"}).status_code == 200


def test_a_derived_token_still_cannot_derive(rig):
    with TestClient(rig.app) as client:
        child = _derive(client, rig.mate, {"home": "crew"}).json()["token"]
        assert _derive(client, child, {"home": "crew"}).status_code == 403


# --- board mail --------------------------------------------------------------


@pytest.fixture
def brain(tmp_path, monkeypatch):
    b = make_brain(tmp_path, monkeypatch, credentials=False)
    b.mate = TokenStore(tmp_path / "serve_tokens.json").add("polly", {"crew": "write"})
    database = Database(tmp_path / "brain.db")
    conn = database.connect()
    from gingugu.config import load_config

    nsm = NamespaceManager(conn, load_config())
    ids = {name: nsm.get_or_create(name).id for name in ("crew", "crow")}
    b.crew_post = board.post(
        conn, namespace_id=ids["crew"], author="beepboop", recipient="polly", body="ahoy"
    )["id"]
    board.post(conn, namespace_id=ids["crow"], author="beepboop", recipient="polly", body="secret")
    board.post(conn, namespace_id=ids["crew"], author="beepboop", recipient="tyrone", body="psst")
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


async def _post(served, path, body, token):
    async with httpx2.AsyncClient(timeout=10) as client:
        return await client.post(
            served.url + path, json=body, headers={"Authorization": f"Bearer {token}"}
        )


@pytest.mark.parametrize("names", [None, ["crew", "crow"]])
async def test_scoped_inbox_is_fenced_to_its_grant(brain, served, names):
    body = {"persona": "polly", "namespaces": names, "shown": []}
    r = await _post(served, "/hook/inbox", body, brain.mate)
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()["posts"]] == [brain.crew_post]


async def test_scoped_inbox_only_for_its_own_persona(brain, served):
    body = {"persona": "tyrone", "namespaces": None, "shown": []}
    r = await _post(served, "/hook/inbox", body, brain.mate)
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/hook/recall", "/hook/tripwires", "/hook/warmup"])
async def test_other_hook_routes_stay_owner_only(brain, served, path):
    r = await _post(served, path, {}, brain.mate)
    assert r.status_code == 403


# --- end to end: the crew mate's proxy on its scoped token ---------------------


async def test_a_crew_mates_proxy_runs_on_its_scoped_token(tmp_path, monkeypatch):
    from tests.proxy_fixtures import payload, serving, through_proxy

    b = make_brain(tmp_path, monkeypatch, credentials=False)
    mate = TokenStore(tmp_path / "serve_tokens.json").add("polly", {"crew": "write"})
    monkeypatch.setenv("MEMORY_PERSONA", "polly")
    async with serving(b.app()) as served:
        async with through_proxy(served.url, mate, home="crew") as (session, _):
            await session.initialize()
            stored = payload(
                await session.call_tool(
                    "memory_store", {"title": "gold", "content": "a lesson", "type": "fact"}
                )
            )
            denied = payload(
                await session.call_tool(
                    "memory_store",
                    {"title": "x", "content": "y", "type": "fact", "namespace": "crow"},
                )
            )
            posted = payload(
                await session.call_tool(
                    "board", {"action": "post", "body": "ahoy crew", "to": "beepboop"}
                )
            )
    assert stored["ok"] and stored["namespace"] == "crew"
    assert not denied["ok"]
    assert posted["ok"] and posted["post"]["author"] == "polly", posted
    assert all(mate not in auth for auth in b.mcp_auth())
