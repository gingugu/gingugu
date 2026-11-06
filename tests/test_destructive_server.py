"""The big-delete gate, server side: ``confirm=true`` alone is not enough.

A local server (stdio) takes the hook's single-use approval for the call, a real
dialog where the client can show one, or ``MEMORY_CONFIRM_DESTRUCTIVE=off``.
Without one it refuses. In remote mode the brain cannot see this machine's
approvals, so the proxy - which runs here - requires one before it relays the
call, and the local vault does the same for ``credential_delete``.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.confirm_fixtures
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp import Client

from gingugu import confirm, destructive
from tests.confirm_fixtures import MODES, Person, call, exists, payload, stored
from tests.proxy_fixtures import make_brain, serving, through_proxy

pytestmark = pytest.mark.asyncio

VSCODE = "claude-vscode"
DOOMED = {"action": "delete", "name": "doomed", "cascade": True}


@pytest.fixture
def full(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "big-delete.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "confirm")
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "true")
    monkeypatch.delenv(confirm.ENV, raising=False)
    from tests.test_embeddings import FakeEmbedder

    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    return build_server()


def _data_dir() -> Path:
    return Path(os.environ["MEMORY_DB_PATH"]).parent


async def _doomed(server) -> str:
    async with Client(server, mode="legacy") as client:
        out = payload(
            await client.call_tool(
                "memory_store",
                {"title": "lives in doomed", "content": "x", "type": "fact", "namespace": "doomed"},
            )
        )
    return out["memory"]["id"]


@pytest.mark.parametrize("mode", MODES)
async def test_a_namespace_delete_without_approval_is_refused(full, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    mid = await _doomed(full)
    out = await call(full, Person(), mode, "memory_namespaces", {**DOOMED, "confirm": True})
    assert out["ok"] is False and "approval" in out["error"]
    assert await exists(full, mid)


@pytest.mark.parametrize("mode", MODES)
async def test_the_hooks_approval_lets_it_through_once(full, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    mid = await _doomed(full)
    destructive.approve(_data_dir(), "memory_namespaces", DOOMED)
    out = await call(full, Person(), mode, "memory_namespaces", {**DOOMED, "confirm": True})
    assert out["ok"] is True and out["memories_removed"] == 1
    assert not await exists(full, mid)
    assert not destructive.take(_data_dir(), "memory_namespaces", DOOMED)  # used up


@pytest.mark.parametrize("mode", MODES)
async def test_without_approval_a_client_that_can_ask_gets_a_dialog(full, mode):
    mid = await _doomed(full)
    person = Person()
    out = await call(full, person, mode, "memory_namespaces", {**DOOMED, "confirm": True})
    assert out["ok"] is True and len(person.asked) == 1
    assert not await exists(full, mid)


@pytest.mark.parametrize("mode", MODES)
async def test_the_opt_out_still_covers_big_deletes(full, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    monkeypatch.setenv(confirm.ENV, "off")
    await _doomed(full)
    out = await call(full, Person(), mode, "memory_namespaces", {**DOOMED, "confirm": True})
    assert out["ok"] is True


@pytest.mark.parametrize("mode", MODES)
async def test_a_bulk_consolidation_needs_approval_but_a_small_one_does_not(
    full, mode, monkeypatch
):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    many = [await stored(full, f"dup {i}") for i in range(destructive.THRESHOLD + 1)]
    args = {"memory_ids": ",".join(many), "keep_originals": False, "confirm": True}
    out = await call(full, Person(), mode, "memory_consolidate", args)
    assert out["ok"] is False and "approval" in out["error"]
    few = [await stored(full, f"pair {i}") for i in range(2)]
    args = {"memory_ids": ",".join(few), "keep_originals": False, "confirm": True}
    assert (await call(full, Person(), mode, "memory_consolidate", args))["ok"] is True


@pytest.mark.parametrize("mode", MODES)
async def test_a_credential_delete_needs_approval(full, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    async with Client(full, mode="legacy") as client:
        await client.call_tool("credential_store", {"service_name": "svc", "fields": fields})
    args = {"service_name": "svc", "confirm": True}
    out = await call(full, Person(), mode, "credential_delete", args)
    assert out["ok"] is False and "approval" in out["error"]
    destructive.approve(_data_dir(), "credential_delete", args)
    assert (await call(full, Person(), mode, "credential_delete", args))["ok"] is True


# --- over HTTP: only the owner's confirm=true is taken at its word -----------------------


@pytest.fixture
def http_brain(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "http.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "doomed")
    monkeypatch.delenv(confirm.ENV, raising=False)
    from tests.test_embeddings import FakeEmbedder

    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    return build_server(transport="http")


def _as(monkeypatch, grant) -> None:
    monkeypatch.setattr("gingugu.handlers.fence.request_grant", lambda *_: grant)


@pytest.mark.parametrize("mode", MODES)
async def test_a_scoped_tokens_big_consolidation_needs_the_person(http_brain, mode, monkeypatch):
    # The owner's proxy checks this machine's approval before relaying; a scoped
    # token (a minion, another client) reaches the brain with nothing checked.
    # The fence already shuts it out of namespace deletes, imports and the vault;
    # a consolidation that drops originals is the big delete it can still reach.
    from gingugu.grants import FULL, WRITE, Grant

    _as(monkeypatch, FULL)
    many = [await stored(http_brain, f"dup {i}") for i in range(destructive.THRESHOLD + 1)]
    args = {"memory_ids": ",".join(many), "keep_originals": False, "confirm": True}
    _as(monkeypatch, Grant("minion", {"doomed": WRITE}))
    out = await call(http_brain, Person(action=None), mode, "memory_consolidate", args)
    assert out["ok"] is False and "approval" in out["error"]
    person = Person(action="decline")
    out = await call(http_brain, person, mode, "memory_consolidate", args)
    assert out["ok"] is False and len(person.asked) == 1  # a client that can ask: the person
    _as(monkeypatch, FULL)
    assert all([await exists(http_brain, mid) for mid in many])
    out = await call(http_brain, Person(action=None), mode, "memory_consolidate", args)
    assert out["ok"] is True  # the owner's word: its proxy took the approval


# --- remote mode: the proxy is where this machine's approvals are checked -----------------


async def test_the_proxy_requires_approval_before_the_brain_hears_of_it(tmp_path, monkeypatch):
    monkeypatch.delenv(confirm.ENV, raising=False)
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    brain = make_brain(tmp_path, monkeypatch)
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url,
            brain.machine,
            vault_db=tmp_path / "local.db",
            session_kwargs={"elicitation_callback": Person()},
        ) as (session, _):
            await session.initialize()

            async def tool(name: str, args: dict) -> dict:
                return payload(await session.call_tool(name, args))

            await tool(
                "memory_store",
                {"title": "remote", "content": "x", "type": "fact", "namespace": "doomed"},
            )
            await tool("credential_store", {"service_name": "svc", "fields": fields})
            ns_args = {**DOOMED, "confirm": True}
            cred_args = {"service_name": "svc", "confirm": True}
            refused_ns = await tool("memory_namespaces", ns_args)
            refused_cred = await tool("credential_delete", cred_args)
            listed = await tool("memory_namespaces", {"action": "list"})
            destructive.approve(_data_dir(), "memory_namespaces", ns_args)
            destructive.approve(_data_dir(), "credential_delete", cred_args)
            done_ns = await tool("memory_namespaces", ns_args)
            done_cred = await tool("credential_delete", cred_args)
    assert refused_ns["ok"] is False and "approval" in refused_ns["error"]
    assert refused_cred["ok"] is False and "approval" in refused_cred["error"]
    assert "doomed" in [n["name"] for n in listed["namespaces"]]
    assert done_ns["ok"] is True and done_cred["ok"] is True
