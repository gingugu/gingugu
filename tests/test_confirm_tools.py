"""Every other destructive path asks the person too, and only when destructive.

``memory_forget`` is covered in ``test_confirm.py``. Here: a cascading namespace
delete, consolidation that drops its originals, an overwriting import, and
``credential_delete``. Each is driven through the SDK client on both eras.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.confirm_fixtures
from __future__ import annotations

import pytest
from mcp import Client

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

NO = [Person(action="decline"), Person(action="cancel"), Person(confirm=False), Person(action=None)]


async def _seed_namespace(server, name: str = "doomed") -> str:
    async with Client(server, mode="legacy") as client:
        out = payload(
            await client.call_tool(
                "memory_store",
                {"title": "lives in doomed", "content": "x", "type": "fact", "namespace": name},
            )
        )
    return out["memory"]["id"]


# --- memory_namespaces delete --------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_a_cascading_namespace_delete_asks_and_names_the_count(server, mode):
    mid = await _seed_namespace(server)
    person = Person()
    out = await call(
        server,
        person,
        mode,
        "memory_namespaces",
        {"action": "delete", "name": "doomed", "cascade": True},
    )
    assert out["ok"] is True and out["memories_removed"] == 1
    assert (
        len(person.asked) == 1 and '"doomed"' in person.asked[0] and "1 memories" in person.asked[0]
    )
    assert not await exists(server, mid)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("person", NO)
async def test_a_refused_namespace_delete_keeps_everything(server, mode, person):
    mid = await _seed_namespace(server)
    out = await call(
        server,
        person,
        mode,
        "memory_namespaces",
        {"action": "delete", "name": "doomed", "cascade": True},
    )
    assert out["ok"] is False
    assert await exists(server, mid)


@pytest.mark.parametrize("mode", MODES)
async def test_an_empty_namespace_delete_and_other_actions_never_ask(server, mode):
    person = Person()
    assert (
        await call(server, person, mode, "memory_namespaces", {"action": "create", "name": "empty"})
    )["ok"]
    out = await call(
        server,
        person,
        mode,
        "memory_namespaces",
        {"action": "delete", "name": "empty", "cascade": True},
    )
    assert out["ok"] is True and out["memories_removed"] == 0
    await call(server, person, mode, "memory_namespaces", {"action": "list"})
    assert person.asked == []


@pytest.mark.parametrize("mode", MODES)
async def test_a_non_cascading_delete_of_a_full_namespace_is_refused_unasked(server, mode):
    await _seed_namespace(server)
    person = Person()
    out = await call(
        server, person, mode, "memory_namespaces", {"action": "delete", "name": "doomed"}
    )
    assert out["ok"] is False and "cascade" in out["error"] and person.asked == []


# --- memory_consolidate --------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_consolidating_without_originals_asks(server, mode):
    a, b = await stored(server, "dup one"), await stored(server, "dup two")
    person = Person()
    out = await call(
        server,
        person,
        mode,
        "memory_consolidate",
        {"memory_ids": f"{a},{b}", "keep_originals": False},
    )
    assert out["ok"] is True and len(person.asked) == 1 and a in person.asked[0]
    assert not await exists(server, a) and not await exists(server, b)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("person", NO)
async def test_a_refused_consolidation_changes_nothing(server, mode, person):
    a, b = await stored(server, "dup one"), await stored(server, "dup two")
    out = await call(
        server,
        person,
        mode,
        "memory_consolidate",
        {"memory_ids": f"{a},{b}", "keep_originals": False},
    )
    assert out["ok"] is False
    assert await exists(server, a) and await exists(server, b)


@pytest.mark.parametrize("mode", MODES)
async def test_consolidating_with_originals_kept_never_asks(server, mode):
    a, b = await stored(server, "dup one"), await stored(server, "dup two")
    person = Person()
    out = await call(server, person, mode, "memory_consolidate", {"memory_ids": f"{a},{b}"})
    assert out["ok"] is True and person.asked == []


# --- memory_import -------------------------------------------------------------------


async def _export(server) -> dict:
    async with Client(server, mode="legacy") as client:
        return payload(await client.call_tool("memory_export", {}))["export"]


@pytest.mark.parametrize("mode", MODES)
async def test_an_overwriting_import_asks_with_the_count(server, mode):
    await stored(server, "one")
    data = await _export(server)
    person = Person()
    out = await call(
        server, person, mode, "memory_import", {"data": data, "on_conflict": "replace"}
    )
    assert (
        out["ok"] is True and len(person.asked) == 1 and "Overwrite 1 existing" in person.asked[0]
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("person", NO)
async def test_a_refused_import_writes_nothing(server, mode, person):
    mid = await stored(server, "original title")
    data = await _export(server)
    data["memories"][0]["title"] = "overwritten title"
    out = await call(
        server, person, mode, "memory_import", {"data": data, "on_conflict": "replace"}
    )
    assert out["ok"] is False
    async with Client(server, mode="legacy") as client:
        found = payload(await client.call_tool("memory_search", {"ids": mid}))["memories"]
    assert found[0]["title"] == "original title"


@pytest.mark.parametrize("mode", MODES)
async def test_skip_imports_and_replace_imports_of_new_ids_never_ask(server, mode):
    await stored(server, "one")
    data = await _export(server)
    person = Person()
    assert (await call(server, person, mode, "memory_import", {"data": data}))["ok"]
    data["memories"][0]["id"] = "brand-new-id"
    assert (
        await call(server, person, mode, "memory_import", {"data": data, "on_conflict": "replace"})
    )["ok"]
    assert person.asked == []


# --- credential_delete ----------------------------------------------------------------


@pytest.fixture
def vault_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "true")
    monkeypatch.delenv("MEMORY_CONFIRM_DESTRUCTIVE", raising=False)
    from tests.test_embeddings import FakeEmbedder

    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    return build_server()


async def _cred(server, service="svc"):
    async with Client(server, mode="legacy") as client:
        await client.call_tool(
            "credential_store", {"service_name": service, "fields": '{"api_key": "k-123"}'}
        )


async def _services(server) -> list[str]:
    async with Client(server, mode="legacy") as client:
        out = payload(await client.call_tool("credential_list", {}))
    return [s["service_name"] for s in out["services"]]


@pytest.mark.parametrize("mode", MODES)
async def test_credential_delete_asks_the_person(vault_server, mode):
    await _cred(vault_server)
    person = Person()
    out = await call(
        vault_server, person, mode, "credential_delete", {"service_name": "svc", "confirm": True}
    )
    assert out["ok"] is True and len(person.asked) == 1 and '"svc"' in person.asked[0]
    assert await _services(vault_server) == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("person", NO)
async def test_a_refused_credential_delete_keeps_the_secret(vault_server, mode, person):
    await _cred(vault_server)
    out = await call(
        vault_server, person, mode, "credential_delete", {"service_name": "svc", "confirm": True}
    )
    assert out["ok"] is False
    assert await _services(vault_server) == ["svc"]


@pytest.mark.parametrize("mode", MODES)
async def test_the_models_confirm_only_ever_says_no(vault_server, mode):
    await _cred(vault_server)
    person = Person()
    out = await call(
        vault_server, person, mode, "credential_delete", {"service_name": "svc", "confirm": False}
    )
    assert out["ok"] is False and person.asked == []
    # Omitted is no longer a schema error: the person decides.
    out = await call(vault_server, person, mode, "credential_delete", {"service_name": "svc"})
    assert out["ok"] is True and len(person.asked) == 1
