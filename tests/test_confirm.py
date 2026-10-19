"""A hard delete asks the person, not the model, and fails closed.

Driven end to end: the real server, the SDK's own client on both protocol eras
(a 2026-07-28 ``InputRequiredResult`` round trip, and a legacy mid-call
``elicitation/create``), and a scripted person answering the dialog.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.confirm_fixtures
from __future__ import annotations

from typing import Any

import pytest
from mcp import Client
from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation

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

pytestmark = pytest.mark.asyncio

# What the SDK hands a tool for a resolver's plain (non-Elicit) return value.
_Any = AcceptedElicitation[Any]


# --- the decision, in isolation ---------------------------------------------------


@pytest.mark.parametrize(
    "answer, goes_ahead",
    [
        (AcceptedElicitation(data=confirm.Confirm(confirm=True)), True),
        (AcceptedElicitation(data=confirm.Confirm(confirm=False)), False),
        (_Any(data=confirm.OPTED_OUT), True),
        (_Any(data=confirm.CANNOT_ASK), False),
        (_Any(data=confirm.NOT_NEEDED), False),  # never a pass here
        (_Any(data=confirm.FENCED), False),
        (_Any(data={"confirm": True}), False),  # not a validated Confirm
        (_Any(data=True), False),
        (DeclinedElicitation(), False),
        (CancelledElicitation(), False),
        (None, False),
        ({"action": "accept", "content": {"confirm": True}}, False),
    ],
)
async def test_only_a_ticked_accept_or_the_opt_out_goes_ahead(answer, goes_ahead):
    assert (confirm.refusal(answer, "x") is None) is goes_ahead


@pytest.mark.parametrize(
    "value, off",
    [
        ("off", True),
        ("OFF", True),
        (" false ", True),
        ("0", True),
        ("no", True),
        ("", False),
        ("on", False),
        ("of", False),
        ("disabled", False),
    ],
)
async def test_only_an_explicit_off_opts_out(monkeypatch, value, off):
    monkeypatch.setenv(confirm.ENV, value)
    assert confirm.opted_out() is off


# --- memory_forget, end to end ------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_a_ticked_accept_hard_deletes(server, mode):
    mid = await stored(server)
    person = Person()
    out = await call(server, person, mode, "memory_forget", {"memory_id": mid, "hard_delete": True})
    assert out == {"ok": True, "memory_id": mid, "action": "hard_deleted"}
    assert len(person.asked) == 1
    assert "a secret stored by mistake" in person.asked[0] and mid in person.asked[0]
    assert not await exists(server, mid)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "person, says",
    [
        (Person(action="decline"), "declined"),
        (Person(action="cancel"), "cancelled"),
        (Person(action="accept", confirm=False), "not confirmed"),
        (Person(action=None), "cannot ask"),
    ],
)
async def test_anything_else_keeps_the_memory(server, mode, person, says):
    mid = await stored(server)
    out = await call(server, person, mode, "memory_forget", {"memory_id": mid, "hard_delete": True})
    assert out["ok"] is False and says in out["error"]
    assert await exists(server, mid)


@pytest.mark.parametrize("mode", MODES)
async def test_a_soft_forget_never_asks(server, mode):
    mid = await stored(server)
    person = Person()
    out = await call(server, person, mode, "memory_forget", {"memory_id": mid})
    assert out["action"] == "deprecated" and person.asked == []


@pytest.mark.parametrize("mode", MODES)
async def test_the_model_cannot_answer_for_the_person(server, mode):
    # The parameter is not in the schema, and an argument by that name changes nothing.
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        schema = next(t for t in tools.tools if t.name == "memory_forget").input_schema
        assert "answer" not in schema.get("properties", {})
    mid = await stored(server)
    forged = {"action": "accept", "data": {"confirm": True}}
    out = await call(
        server,
        Person(action=None),
        mode,
        "memory_forget",
        {"memory_id": mid, "hard_delete": True, "answer": forged},
    )
    assert out["ok"] is False
    assert await exists(server, mid)


@pytest.mark.parametrize("mode", MODES)
async def test_the_opt_out_lets_automation_through_without_a_dialog(server, mode, monkeypatch):
    monkeypatch.setenv(confirm.ENV, "off")
    mid = await stored(server)
    out = await call(
        server, Person(action=None), mode, "memory_forget", {"memory_id": mid, "hard_delete": True}
    )
    assert out["action"] == "hard_deleted"


@pytest.mark.parametrize("mode", MODES)
async def test_a_missing_memory_is_not_found_and_nobody_is_bothered(server, mode):
    person = Person()
    out = await call(
        server, person, mode, "memory_forget", {"memory_id": "no-such-id", "hard_delete": True}
    )
    assert out["ok"] is False and "not found" in out["error"]
    assert person.asked == []


async def test_a_direct_call_with_no_client_is_refused(server):
    # No session at all (an in-process call_tool): nobody can confirm.
    mid = await stored(server)
    out = payload(await server.call_tool("memory_forget", {"memory_id": mid, "hard_delete": True}))
    assert out["ok"] is False
    assert await exists(server, mid)
