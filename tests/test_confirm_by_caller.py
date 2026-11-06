"""The caller's ``confirm=true`` is the confirmation, on every destructive path.

A client with no dialog (Claude Code's VS Code extension, a headless SDK run)
must never dead-end a delete into "go run something in a terminal". The agent
asks the user in chat and the harness permission prompt gates the call, so an
explicit ``confirm=true`` goes ahead without a dialog. Omitted, a client that can
ask still shows one; one that cannot is told to call again with ``confirm=true``.
``confirm=false`` always refuses, and the grant fence still comes first.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.confirm_fixtures
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp import Client

from gingugu import confirm, destructive
from tests.confirm_fixtures import (  # noqa: F401 - server is a fixture
    MODES,
    Person,
    call,
    exists,
    payload,
    server,
    stored,
)
from tests.proxy_fixtures import make_brain, serving, through_proxy

pytestmark = pytest.mark.asyncio

VSCODE = "claude-vscode"
DESTRUCTIVE = (
    "memory_forget",
    "memory_consolidate",
    "memory_namespaces",
    "memory_import",
    "credential_delete",
)


@pytest.fixture
def full(tmp_path, monkeypatch):
    """One server with the vault on, so every destructive tool is reachable."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "by-caller.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "confirm")
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "true")
    monkeypatch.delenv(confirm.ENV, raising=False)
    from tests.test_embeddings import FakeEmbedder

    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    return build_server()


async def _legacy(server, tool: str, args: dict) -> dict:
    async with Client(server, mode="legacy") as client:
        return payload(await client.call_tool(tool, args))


def _approve(tool: str, args: dict) -> None:
    """What gingugu's hook records when it asks the person (``destructive``)."""
    destructive.approve(Path(os.environ["MEMORY_DB_PATH"]).parent, tool, args)


# Each case seeds what it will destroy, names the call, and says whether it is gone.


async def _forget(server):
    mid = await stored(server)

    async def gone() -> bool:
        return not await exists(server, mid)

    return "memory_forget", {"memory_id": mid, "hard_delete": True}, gone


async def _consolidate(server):
    a, b = await stored(server, "dup one"), await stored(server, "dup two")

    async def gone() -> bool:
        return not await exists(server, a) and not await exists(server, b)

    return "memory_consolidate", {"memory_ids": f"{a},{b}", "keep_originals": False}, gone


async def _namespace(server):
    out = await _legacy(
        server,
        "memory_store",
        {"title": "lives in doomed", "content": "x", "type": "fact", "namespace": "doomed"},
    )
    mid = out["memory"]["id"]
    args = {"action": "delete", "name": "doomed", "cascade": True}
    _approve("memory_namespaces", args)  # a big delete: the hook's approval stands in

    async def gone() -> bool:
        return not await exists(server, mid)

    return "memory_namespaces", args, gone


async def _import(server):
    mid = await stored(server, "original title")
    data = (await _legacy(server, "memory_export", {}))["export"]
    data["memories"][0]["title"] = "overwritten title"

    async def gone() -> bool:
        found = (await _legacy(server, "memory_search", {"ids": mid}))["memories"]
        return found[0]["title"] == "overwritten title"

    return "memory_import", {"data": data, "on_conflict": "replace"}, gone


async def _credential(server):
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    await _legacy(server, "credential_store", {"service_name": "kraken-api", "fields": fields})
    _approve("credential_delete", {"service_name": "kraken-api"})

    async def gone() -> bool:
        listed = (await _legacy(server, "credential_list", {}))["services"]
        return "kraken-api" not in [s["service_name"] for s in listed]

    return "credential_delete", {"service_name": "kraken-api"}, gone


CASES = {
    "memory_forget": _forget,
    "memory_consolidate": _consolidate,
    "memory_namespaces": _namespace,
    "memory_import": _import,
    "credential_delete": _credential,
}


# --- the schema ------------------------------------------------------------------------


async def test_every_destructive_tool_offers_the_model_a_confirm(full):
    async with Client(full, mode="legacy") as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in DESTRUCTIVE:
        props = tools[name].input_schema["properties"]
        assert "confirm" in props, name
        assert "answer" not in props, name  # the dialog's slot stays hidden


# --- confirm=true goes ahead, with or without a dialog ---------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_confirm_true_goes_ahead_where_no_dialog_can_be_shown(full, mode, tool, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    name, args, gone = await CASES[tool](full)
    person = Person()
    out = await call(full, person, mode, name, {**args, "confirm": True})
    assert out["ok"] is True, out
    assert person.asked == []
    assert await gone()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_confirm_true_needs_no_dialog_even_where_one_could_be_shown(full, mode, tool):
    name, args, gone = await CASES[tool](full)
    person = Person(action="decline")  # would refuse, if anyone asked
    out = await call(full, person, mode, name, {**args, "confirm": True})
    assert out["ok"] is True, out
    assert person.asked == []
    assert await gone()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_confirm_true_goes_ahead_for_a_client_with_no_elicitation(full, mode, tool):
    name, args, gone = await CASES[tool](full)
    out = await call(full, Person(action=None), mode, name, {**args, "confirm": True})
    assert out["ok"] is True, out
    assert await gone()


# --- omitted: ask if possible, otherwise say how to proceed, never "use a terminal" ----


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_omitted_under_vscode_says_to_call_again_with_confirm(full, mode, tool, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    name, args, gone = await CASES[tool](full)
    person = Person()
    out = await call(full, person, mode, name, args)
    assert out["ok"] is False
    assert "confirm=true" in out["error"]
    assert "terminal" not in out["error"] and "gingugu credential" not in out["error"]
    assert person.asked == []
    assert not await gone()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_omitted_with_a_dialog_still_asks_the_person(full, mode, tool):
    name, args, gone = await CASES[tool](full)
    person = Person()
    out = await call(full, person, mode, name, args)
    assert out["ok"] is True, out
    assert len(person.asked) == 1
    assert await gone()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_a_person_who_declines_is_not_overridden(full, mode, tool):
    name, args, gone = await CASES[tool](full)
    out = await call(full, Person(action="decline"), mode, name, args)
    assert out["ok"] is False and "declined" in out["error"]
    assert not await gone()


# --- confirm=false refuses, unasked -----------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", DESTRUCTIVE)
async def test_confirm_false_refuses_without_a_dialog(full, mode, tool):
    name, args, gone = await CASES[tool](full)
    person = Person()
    out = await call(full, person, mode, name, {**args, "confirm": False})
    assert out["ok"] is False
    assert person.asked == []
    assert not await gone()


# --- the fence still comes first --------------------------------------------------------


async def test_confirm_true_does_not_get_past_the_grant_fence(full, tmp_path, monkeypatch):
    mid = await stored(full)
    monkeypatch.setenv("MEMORY_GRANT", "elsewhere=write")
    from gingugu.server import build_server

    fenced = build_server()
    args = {"memory_id": mid, "hard_delete": True, "confirm": True}
    out = await call(fenced, Person(), "legacy", "memory_forget", args)
    assert out["ok"] is False
    monkeypatch.delenv("MEMORY_GRANT")
    assert await exists(full, mid)


# --- through the proxy: the brain and the local vault both honour it ----------------------


async def test_confirm_true_reaches_the_brain_and_the_vault_through_the_proxy(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(confirm.ENV, raising=False)
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    brain = make_brain(tmp_path, monkeypatch)
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    person = Person()
    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url,
            brain.machine,
            vault_db=tmp_path / "local.db",
            session_kwargs={"elicitation_callback": person},
        ) as (session, _):
            await session.initialize()
            mem = payload(
                await session.call_tool(
                    "memory_store", {"title": "remote secret", "content": "x", "type": "fact"}
                )
            )["memory"]["id"]
            forgot = payload(
                await session.call_tool(
                    "memory_forget", {"memory_id": mem, "hard_delete": True, "confirm": True}
                )
            )
            await session.call_tool(
                "credential_store", {"service_name": "kraken-api", "fields": fields}
            )
            _approve("credential_delete", {"service_name": "kraken-api", "confirm": True})
            deleted = payload(
                await session.call_tool(
                    "credential_delete", {"service_name": "kraken-api", "confirm": True}
                )
            )
    assert forgot["ok"] is True and forgot["action"] == "hard_deleted"
    assert deleted["ok"] is True
    assert person.asked == []
