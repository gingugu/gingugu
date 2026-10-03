"""Derived session tokens and a grant's home namespace.

A machine's owner token never goes on an MCP request in remote mode: the proxy
trades it for a short-lived derived token carrying the client's grant and its
home namespace. Derived tokens live only in the server's memory (hashes, never
plaintext) and die on expiry or restart.
"""

from __future__ import annotations

import pytest

from gingugu import grants
from gingugu.derived_tokens import MAX_LIVE, MAX_TTL, DerivedTokens
from gingugu.grants import FULL, READ, WRITE, Grant


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return DerivedTokens(clock=clock)


TYRONE = Grant("derived:tyrone", {"tyrone": WRITE, "crow": READ}, home="tyrone")


# --- the store ---------------------------------------------------------------


def test_mint_then_resolve_round_trips_grant_and_home(store):
    token = store.mint(TYRONE, ttl=600)
    assert store.resolve(token) == TYRONE
    assert store.resolve(token).home == "tyrone"


def test_unknown_and_empty_tokens_resolve_to_none(store):
    store.mint(TYRONE, ttl=600)
    assert store.resolve("nope") is None
    assert store.resolve("") is None


def test_token_expires(store, clock):
    token = store.mint(TYRONE, ttl=60)
    clock.now += 59
    assert store.resolve(token) == TYRONE
    clock.now += 2
    assert store.resolve(token) is None


@pytest.mark.parametrize("ttl", [0, -1, MAX_TTL + 1])
def test_ttl_out_of_bounds_is_refused(store, ttl):
    with pytest.raises(ValueError):
        store.mint(TYRONE, ttl=ttl)


def test_store_never_holds_the_plaintext(store):
    token = store.mint(TYRONE, ttl=600)
    assert token not in repr(vars(store))


def test_tokens_are_distinct(store):
    assert store.mint(TYRONE, ttl=600) != store.mint(TYRONE, ttl=600)


def test_expired_tokens_are_pruned_and_the_live_count_is_capped(store, clock):
    for _ in range(MAX_LIVE):
        store.mint(TYRONE, ttl=60)
    with pytest.raises(OverflowError):
        store.mint(TYRONE, ttl=60)
    clock.now += 61  # all expired: room again
    store.mint(TYRONE, ttl=60)
    assert store.live() == 1


# --- home on a grant ---------------------------------------------------------


def test_home_defaults_to_none_and_full_is_unchanged():
    assert FULL.home is None
    assert Grant("x", {"a": READ}).home is None


def test_empty_home_is_invalid():
    with pytest.raises(ValueError):
        Grant("x", {"a": READ}, home="")


def test_bind_exposes_home_even_for_a_full_grant(db):
    full_home = Grant("derived:me", {"*": WRITE}, home="gingugu")
    assert grants.home() is None
    with grants.bind(full_home, db.conn):
        assert grants.home() == "gingugu"
        assert grants.current() is None  # still unfenced
    assert grants.home() is None


def test_bind_exposes_home_and_fence_for_a_scoped_grant(db):
    with grants.bind(TYRONE, db.conn):
        assert grants.home() == "tyrone"
        assert grants.current() == TYRONE
    assert grants.home() is None and grants.current() is None


# --- home drives the default namespace ---------------------------------------


def test_resolve_name_prefers_the_home_over_the_server_config(db, monkeypatch):
    from gingugu.config import load_config
    from gingugu.namespaces import NamespaceManager

    monkeypatch.setenv("MEMORY_NAMESPACE", "server-default")
    manager = NamespaceManager(db.conn, load_config())
    assert manager.resolve_name() == "server-default"
    with grants.bind(Grant("derived:me", {"*": WRITE}, home="gingugu"), db.conn):
        assert manager.resolve_name() == "gingugu"
        assert manager.resolve_name("explicit") == "explicit"
