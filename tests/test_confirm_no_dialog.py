"""A client that declares elicitation but shows no dialog is a client that cannot ask.

Claude Code's VS Code extension advertises form elicitation, then answers every
``elicitation/create`` with ``decline`` without showing the person anything.
Taken at face value that reads as "the user declined" - a refusal blamed on
someone who never saw a question. It names itself to the servers it launches
in ``CLAUDE_CODE_ENTRYPOINT``, so a stdio server, and the proxy on behalf of
the brain behind it, treat it as the client that cannot ask that it is.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.confirm_fixtures
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from mcp import types
from mcp.shared.message import SessionMessage

from gingugu import confirm
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


def _ctx(elicitation: types.ElicitationCapability | None):
    return SimpleNamespace(client_capabilities=types.ClientCapabilities(elicitation=elicitation))


ASKS = types.ElicitationCapability(form=types.FormElicitationCapability())


# --- the rule --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entrypoint, shows",
    [(None, True), ("cli", True), ("claude-desktop", True), ("sdk-py", True), (VSCODE, False)],
)
async def test_only_a_client_known_to_show_no_dialog_is_taken_at_its_word(
    monkeypatch, entrypoint, shows
):
    if entrypoint is None:
        monkeypatch.delenv(confirm.ENTRYPOINT, raising=False)
    else:
        monkeypatch.setenv(confirm.ENTRYPOINT, entrypoint)
    assert confirm.can_ask(_ctx(ASKS), "stdio") is shows


async def test_over_http_this_processs_environment_says_nothing_about_the_client(monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    assert confirm.can_ask(_ctx(ASKS), "http") is True


async def test_a_client_with_no_elicitation_still_cannot_ask(monkeypatch):
    assert confirm.can_ask(_ctx(None), "stdio") is False


def _initialize(capabilities: dict) -> SessionMessage:
    params = {
        "protocolVersion": "2025-11-25",
        "capabilities": capabilities,
        "clientInfo": {"name": "claude-code", "version": "2.1.312"},
    }
    return SessionMessage(
        types.JSONRPCRequest(jsonrpc="2.0", id=0, method="initialize", params=params)
    )


async def test_the_relay_withholds_elicitation_only_from_a_client_that_shows_no_dialog(
    monkeypatch,
):
    caps = {"elicitation": {"form": {}}, "roots": {"listChanged": True}}
    item = _initialize(caps)
    assert confirm.withhold_dialogs(item) is item  # any other client: untouched

    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    out = confirm.withhold_dialogs(item).message.params
    assert out["capabilities"] == {"roots": {"listChanged": True}}
    assert out["clientInfo"] == item.message.params["clientInfo"]
    assert item.message.params["capabilities"] == caps  # the original is not mutated


async def test_the_relay_leaves_an_initialize_with_no_elicitation_alone(monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    item = _initialize({"roots": {}})
    assert confirm.withhold_dialogs(item) is item


# --- a local stdio server ----------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_a_hard_delete_under_vscode_is_refused_as_cannot_ask(server, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    mid = await stored(server)
    person = Person()  # would accept, if anyone asked
    out = await call(server, person, mode, "memory_forget", {"memory_id": mid, "hard_delete": True})
    assert out["ok"] is False and "cannot ask" in out["error"]
    assert "declined" not in out["error"]
    assert person.asked == []
    assert await exists(server, mid)


@pytest.mark.parametrize("mode", MODES)
async def test_the_opt_out_still_lets_automation_through_under_vscode(server, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    monkeypatch.setenv(confirm.ENV, "off")
    mid = await stored(server)
    out = await call(
        server, Person(), mode, "memory_forget", {"memory_id": mid, "hard_delete": True}
    )
    assert out["action"] == "hard_deleted"


async def _store_cred(server, person, mode, name="kraken-api"):
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    args = {"service_name": name, "fields": fields}
    assert (await call(server, person, mode, "credential_store", args))["ok"]


@pytest.mark.parametrize("mode", MODES)
async def test_a_refused_credential_delete_points_at_the_terminal(server, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    person = Person()
    await _store_cred(server, person, mode)
    out = await call(server, person, mode, "credential_delete", {"service_name": "kraken-api"})
    left = await call(server, person, mode, "credential_list", {})
    assert out["ok"] is False and "cannot ask" in out["error"]
    assert "gingugu credential delete kraken-api" in out["error"]
    assert person.asked == []
    assert [s["service_name"] for s in left["services"]] == ["kraken-api"]


@pytest.mark.parametrize("mode", MODES)
async def test_the_terminal_hint_quotes_what_the_model_named(server, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    person = Person()
    await _store_cred(server, person, mode, name="a b;rm")
    out = await call(
        server,
        person,
        mode,
        "credential_delete",
        {"service_name": "a b;rm", "field_name": "api_token"},
    )
    assert "gingugu credential delete 'a b;rm' --field api_token" in out["error"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", ["a`b", "a\u202eb", "a\nb"])
async def test_no_terminal_hint_for_a_name_that_would_not_show_as_itself(
    server, mode, monkeypatch, name
):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    person = Person()
    await _store_cred(server, person, mode, name=name)
    out = await call(server, person, mode, "credential_delete", {"service_name": name})
    assert out["ok"] is False and "cannot ask" in out["error"]
    assert "gingugu credential" not in out["error"]


# --- through the proxy -------------------------------------------------------------------


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.delenv(confirm.ENV, raising=False)
    return make_brain(tmp_path, monkeypatch)


async def test_the_brain_behind_the_proxy_is_told_the_client_cannot_ask(brain, monkeypatch):
    """The brain serves HTTP and ignores this process's environment, so only the
    proxy withholding elicitation from the initialize it forwards can make this
    refusal "cannot ask" rather than the client's unasked "decline"."""
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    person = Person()
    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url, brain.machine, session_kwargs={"elicitation_callback": person}
        ) as (session, _):
            await session.initialize()
            stored_out = payload(
                await session.call_tool(
                    "memory_store", {"title": "remote secret", "content": "x", "type": "fact"}
                )
            )
            mid = stored_out["memory"]["id"]
            out = payload(
                await session.call_tool("memory_forget", {"memory_id": mid, "hard_delete": True})
            )
            found = payload(await session.call_tool("memory_search", {"ids": mid}))["memories"]
    assert out["ok"] is False and "cannot ask" in out["error"]
    assert person.asked == []
    assert len(found) == 1


async def test_the_proxys_vault_refuses_under_vscode_and_points_at_the_terminal(
    brain, tmp_path, monkeypatch
):
    monkeypatch.setenv(confirm.ENTRYPOINT, VSCODE)
    person = Person()
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url,
            brain.machine,
            vault_db=tmp_path / "local.db",
            session_kwargs={"elicitation_callback": person},
        ) as (session, _):
            await session.initialize()
            await session.call_tool(
                "credential_store", {"service_name": "kraken-api", "fields": fields}
            )
            out = payload(
                await session.call_tool("credential_delete", {"service_name": "kraken-api"})
            )
    assert out["ok"] is False and "cannot ask" in out["error"]
    assert "gingugu credential delete kraken-api" in out["error"]
    assert person.asked == []
