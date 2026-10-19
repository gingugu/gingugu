"""Confirmations work in remote mode, from the brain and from the local vault.

The proxy speaks the legacy handshake to its client (it refuses the 2026
``server/discover`` probe), so a confirmation arrives as a mid-call
``elicitation/create`` request from whichever server is running the tool:

- the brain, for ``memory_forget`` and friends - relayed brain -> client, and
  the person's answer relayed back;
- the proxy's own vault, for ``credential_delete`` - which therefore needs a
  real MCP session (the client's capabilities), must not block the one reader
  of the client while it waits, and must get the answer to *its* question even
  though the brain numbers its requests the same way.
"""

from __future__ import annotations

import json

import anyio
import pytest

from tests.confirm_fixtures import Person
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy

pytestmark = pytest.mark.asyncio


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_CONFIRM_DESTRUCTIVE", raising=False)
    return make_brain(tmp_path, monkeypatch)


@pytest.fixture
def local_db(tmp_path):
    return tmp_path / "local.db"


def _asking(person: Person | None) -> dict:
    return {} if person is None else {"elicitation_callback": person}


async def _memory(session) -> str:
    out = payload(
        await session.call_tool(
            "memory_store", {"title": "remote secret", "content": "x", "type": "fact"}
        )
    )
    return out["memory"]["id"]


async def _cred(session, name: str = "kraken-api") -> None:
    fields = json.dumps({"api_token": {"value": "sk-" + "k" * 30, "is_secret": True}})
    assert payload(
        await session.call_tool("credential_store", {"service_name": name, "fields": fields})
    )["ok"]


async def _services(session) -> list[str]:
    out = payload(await session.call_tool("credential_list", {}))
    return [s["service_name"] for s in out["services"]]


# --- the brain asks, through the proxy ------------------------------------------------


@pytest.mark.parametrize("action, deleted", [("accept", True), ("decline", False)])
async def test_a_brain_confirmation_reaches_the_person_and_back(brain, action, deleted):
    person = Person(action=action)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, session_kwargs=_asking(person)) as (
            session,
            _,
        ):
            await session.initialize()
            mid = await _memory(session)
            out = payload(
                await session.call_tool("memory_forget", {"memory_id": mid, "hard_delete": True})
            )
            found = payload(await session.call_tool("memory_search", {"ids": mid}))["memories"]
    assert len(person.asked) == 1 and "remote secret" in person.asked[0]
    assert out["ok"] is deleted
    assert (found == []) is deleted


async def test_a_client_that_cannot_ask_gets_the_brains_refusal(brain):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine) as (session, _):
            await session.initialize()
            mid = await _memory(session)
            out = payload(
                await session.call_tool("memory_forget", {"memory_id": mid, "hard_delete": True})
            )
    assert out["ok"] is False and "cannot ask" in out["error"]


# --- the local vault asks --------------------------------------------------------------


@pytest.mark.parametrize(
    "person, kept",
    [
        (Person(), False),
        (Person(action="decline"), True),
        (Person(action="cancel"), True),
        (Person(confirm=False), True),
        (None, True),  # no elicitation capability at all
    ],
)
async def test_the_vault_asks_the_person_before_deleting(brain, local_db, person, kept):
    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url, brain.machine, vault_db=local_db, session_kwargs=_asking(person)
        ) as (session, _):
            await session.initialize()
            await _cred(session)
            out = payload(
                await session.call_tool("credential_delete", {"service_name": "kraken-api"})
            )
            left = await _services(session)
    assert out["ok"] is not kept
    assert left == (["kraken-api"] if kept else [])
    if person is not None:
        assert len(person.asked) == 1 and '"kraken-api"' in person.asked[0]


async def test_the_vault_opt_out_is_read_on_this_machine(brain, local_db, monkeypatch):
    monkeypatch.setenv("MEMORY_CONFIRM_DESTRUCTIVE", "off")  # the proxy's env, not the brain's
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            await _cred(session)
            out = payload(
                await session.call_tool("credential_delete", {"service_name": "kraken-api"})
            )
    assert out["ok"] is True


async def test_waiting_on_the_person_does_not_stall_the_brain(brain, local_db):
    """While the vault's question is open, the client's other calls still flow."""
    release = anyio.Event()
    answered: list[str] = []

    async def slow(context, params):
        await release.wait()
        return Person().result()

    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url,
            brain.machine,
            vault_db=local_db,
            session_kwargs={"elicitation_callback": slow},
        ) as (session, _):
            await session.initialize()
            await _cred(session)
            async with anyio.create_task_group() as tg:

                async def delete() -> None:
                    out = payload(
                        await session.call_tool("credential_delete", {"service_name": "kraken-api"})
                    )
                    answered.append("delete" if out["ok"] else "refused")

                tg.start_soon(delete)
                with anyio.fail_after(10):
                    stats = payload(await session.call_tool("memory_stats", {}))
                answered.append("stats" if stats["ok"] else "stats-failed")
                release.set()
    assert answered == ["stats", "delete"]


async def test_brain_and_vault_questions_open_at_once_each_get_their_own_answer(brain, local_db):
    """Both servers number their first request alike (measured: both send id 1), so
    the answers must be routed by more than the id the client echoes. The brain's
    question is answered first: without the vault's ids renamed, its "accept"
    would be claimed by the vault and delete the credential."""
    both = anyio.Event()
    brain_answered = anyio.Event()
    seen: list[str] = []

    async def person(context, params):
        seen.append(params.message)
        if len(seen) == 2:
            both.set()
        await both.wait()
        if "credential" in params.message:
            await brain_answered.wait()
            await anyio.sleep(0.3)  # let the brain's answer reach the proxy first
            return Person(action="decline").result()
        brain_answered.set()
        return Person().result()

    async with serving(brain.app()) as served:
        async with through_proxy(
            served.url,
            brain.machine,
            vault_db=local_db,
            session_kwargs={"elicitation_callback": person},
        ) as (session, _):
            await session.initialize()
            mid = await _memory(session)
            await _cred(session)
            results: dict[str, dict] = {}
            async with anyio.create_task_group() as tg:

                async def forget() -> None:
                    results["forget"] = payload(
                        await session.call_tool(
                            "memory_forget", {"memory_id": mid, "hard_delete": True}
                        )
                    )

                async def delete() -> None:
                    results["delete"] = payload(
                        await session.call_tool("credential_delete", {"service_name": "kraken-api"})
                    )

                with anyio.fail_after(15):
                    tg.start_soon(forget)
                    tg.start_soon(delete)
            left = await _services(session)
    assert len(seen) == 2
    assert results["forget"]["ok"] is True  # accepted: the memory went
    assert results["delete"]["ok"] is False and "declined" in results["delete"]["error"]
    assert left == ["kraken-api"]
