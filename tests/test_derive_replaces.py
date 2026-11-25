"""A renewing proxy hands back the token it is renewing: one live token per proxy.

The proxy re-derives at half its token's TTL. Without ``replaces`` the old
token stayed live until it expired, so every proxy held two of its caller's
live tokens - and a crew mate's scoped token, capped at ``SCOPED_LIVE_MAX``,
was locked out (429) by its fifth open session. With it, the brain drops the
old token in the same step it mints the new one, before the cap is checked.

Only a token the same caller derived can be replaced: anything else named in
``replaces`` - another caller's, unknown, expired - is ignored, and the derive
goes ahead as if it were absent.
"""

from __future__ import annotations

import anyio
import httpx2
import pytest
from starlette.testclient import TestClient

from gingugu.derived_tokens import DerivedTokens
from gingugu.grants import READ, WRITE, Grant
from gingugu.serve_derive import SCOPED_LIVE_MAX
from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import Served, free_port, make_brain, payload, through_proxy
from tests.test_embeddings import FakeEmbedder

SERVE_TOKEN = "serve-owner-" + "s" * 32
DERIVE = "/token/derive"
MATE = Grant("derived:polly", {"crew": WRITE}, home="crew", derived=True, parent="polly")


# --- the store ---------------------------------------------------------------


def test_replacing_drops_the_old_token_in_the_same_mint():
    store = DerivedTokens()
    old = store.mint(MATE, 60, by="k1")
    new = store.mint(MATE, 60, by="k1", replaces=old)
    assert store.resolve(old) is None
    assert store.resolve(new) is not None
    assert store.live() == 1


def test_a_renewal_at_the_per_parent_cap_succeeds():
    store = DerivedTokens()
    held = [store.mint(MATE, 60, per_parent=2, by="k1") for _ in range(2)]
    with pytest.raises(OverflowError):
        store.mint(MATE, 60, per_parent=2, by="k1")
    store.mint(MATE, 60, per_parent=2, by="k1", replaces=held[0])
    assert store.live() == 2


@pytest.mark.parametrize("replaces", ["never-minted", ""])
def test_an_unknown_replaces_is_ignored(replaces):
    store = DerivedTokens()
    kept = store.mint(MATE, 60, by="k1")
    store.mint(MATE, 60, by="k1", replaces=replaces)
    assert store.resolve(kept) is not None
    assert store.live() == 2


def test_another_callers_token_is_never_replaced():
    store = DerivedTokens()
    theirs = store.mint(Grant("derived:tyrone", {"tyrone": READ}, derived=True), 60, by="k2")
    store.mint(MATE, 60, by="k1", replaces=theirs)
    assert store.resolve(theirs) is not None
    assert store.live() == 2


def test_a_token_minted_with_no_caller_is_never_replaced():
    store = DerivedTokens()
    anon = store.mint(MATE, 60)
    store.mint(MATE, 60, by="k1", replaces=anon)
    assert store.resolve(anon) is not None


# --- the route ---------------------------------------------------------------


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
    r.mate = tokens.add("polly", {"crew": "write"})
    r.other_mate = tokens.add("vesper", {"crew": "write"})
    r.machine = tokens.add_owner("macbookpro")
    return r


def _derive(client, token, body) -> httpx2.Response:
    return client.post(DERIVE, headers={"Authorization": f"Bearer {token}"}, json=body)


def _mcp_status(client, token) -> int:
    return client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={}).status_code


def test_a_scoped_token_renews_at_its_cap_and_the_old_token_dies(rig):
    with TestClient(rig.app) as client:
        held = [
            _derive(client, rig.mate, {"home": "crew"}).json()["token"]
            for _ in range(SCOPED_LIVE_MAX)
        ]
        assert _derive(client, rig.mate, {"home": "crew"}).status_code == 429
        resp = _derive(client, rig.mate, {"home": "crew", "replaces": held[0]})
        assert resp.status_code == 200, resp.text
        assert _mcp_status(client, held[0]) == 401
        assert _mcp_status(client, resp.json()["token"]) != 401
    assert rig.derived.live() == SCOPED_LIVE_MAX


def test_an_owner_renewal_replaces_its_own_token(rig):
    with TestClient(rig.app) as client:
        old = _derive(client, rig.machine, {"home": "gingugu"}).json()["token"]
        resp = _derive(client, rig.machine, {"home": "gingugu", "replaces": old})
        assert resp.status_code == 200, resp.text
        assert _mcp_status(client, old) == 401
    assert rig.derived.live() == 1


@pytest.mark.parametrize("thief", ["other_mate", "machine"])
def test_a_caller_cannot_replace_a_token_another_caller_derived(rig, thief):
    with TestClient(rig.app) as client:
        victim = _derive(client, rig.mate, {"home": "crew"}).json()["token"]
        home = "crew" if thief == "other_mate" else "gingugu"
        resp = _derive(client, getattr(rig, thief), {"home": home, "replaces": victim})
        assert resp.status_code == 200, resp.text
        assert _mcp_status(client, victim) != 401


@pytest.mark.parametrize("replaces", [7, ["x"], {"t": "x"}, True])
def test_a_non_string_replaces_is_a_bad_request(rig, replaces):
    with TestClient(rig.app) as client:
        resp = _derive(client, rig.mate, {"home": "crew", "replaces": replaces})
    assert resp.status_code == 400, resp.text
    assert rig.derived.live() == 0


# --- the proxy ---------------------------------------------------------------


async def test_a_refreshing_proxy_holds_one_live_token(tmp_path, monkeypatch):
    brain = make_brain(tmp_path, monkeypatch, credentials=False)
    mate = TokenStore(tmp_path / "serve_tokens.json").add("polly", {"crew": "write"})
    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, mate, home="crew", ttl=2) as (s, _):
            await s.initialize()
            await anyio.sleep(3.2)  # three refreshes at half of a 2s TTL
            assert payload(await s.call_tool("memory_stats", {}))["ok"]
            assert brain.derived.live() == 1
    finally:
        await served.stop()


# --- review fixes: a refused renewal keeps the old token; renewals serialise ---


def test_a_renewal_refused_at_a_cap_keeps_the_old_token():
    store = DerivedTokens()
    held = [store.mint(MATE, 60, per_parent=3, by="k1") for _ in range(3)]
    with pytest.raises(OverflowError):  # a lower cap: still over it after the drop
        store.mint(MATE, 60, per_parent=1, by="k1", replaces=held[0])
    assert store.resolve(held[0]) is not None
    assert store.live() == 3


async def test_concurrent_renewals_derive_once(monkeypatch):
    from gingugu import proxy_auth

    calls: list[str | None] = []

    async def fake_derive(url, owner, *, replaces=None, **_):
        calls.append(replaces)
        await anyio.sleep(0.05)
        return f"t{len(calls)}", 60

    monkeypatch.setattr(proxy_auth, "derive", fake_derive)
    tokens = proxy_auth.SessionTokens("https://brain", "owner", {})
    async with anyio.create_task_group() as tg:
        for _ in range(3):
            tg.start_soon(tokens.current)
    assert calls == [None]
    tokens.expiry = 0.0  # past half its life: the next callers must renew
    async with anyio.create_task_group() as tg:
        tg.start_soon(tokens.current)
        tg.start_soon(tokens.derive)
    assert calls[1] == "t1" and calls[2] == "t2"  # each renewal hands back the live one
