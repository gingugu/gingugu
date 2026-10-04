"""Remote mode keeps the credential vault on this machine.

With credentials enabled and no MEMORY_GRANT, the proxy serves ``credential_*``
itself, from the local DB's vault metadata and this machine's keychain, and
relays everything else to the brain. The brain never sees a credential call,
and the brain's own ``credential_*`` tools (if it exposes any) never reach the
client.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat

import anyio
import pytest

from tests.proxy_fixtures import make_brain, payload, serving, through_proxy

_CRED_TOOLS = {"credential_store", "credential_get", "credential_list", "credential_delete"}
_SECRET = "sk-kraken-" + "s" * 24


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch)  # credential tools ON server-side


@pytest.fixture
def local_db(tmp_path):
    return tmp_path / "local.db"


def _vault_rows(db) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM credential_services").fetchone()[0]


async def _store(session, name: str = "kraken-api") -> dict:
    fields = json.dumps({"api_token": {"value": _SECRET, "is_secret": True}})
    return payload(
        await session.call_tool("credential_store", {"service_name": name, "fields": fields})
    )


async def test_tools_are_the_brains_plus_the_local_vault_each_once(brain, local_db):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            tools = (await session.list_tools()).tools
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "a tool is listed twice"
    assert _CRED_TOOLS <= set(names)
    assert "memory_store" in names and "memory_recall" in names
    get = next(t for t in tools if t.name == "credential_get")
    assert "into" in get.inputSchema["properties"]


async def test_a_credential_call_is_answered_locally_and_never_relayed(brain, local_db):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            await session.list_tools()
            relayed = len(brain.mcp_auth())
            stored = await _store(session)
            listed = payload(await session.call_tool("credential_list", {}))
            got = payload(await session.call_tool("credential_get", {"service_name": "kraken-api"}))
            assert len(brain.mcp_auth()) == relayed, "a credential call reached the brain"
            # The relay still works after local calls.
            assert payload(await session.call_tool("memory_stats", {}))["ok"]
    assert stored["ok"] and listed["ok"] and listed["count"] == 1
    assert got["ok"] and _SECRET not in json.dumps(got)  # redacted by default
    assert _vault_rows(local_db) == 1
    assert _vault_rows(brain.tmp_path / "brain.db") == 0


async def test_into_writes_a_0600_file_on_this_machine(brain, local_db, tmp_path):
    out = tmp_path / "secret.txt"
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            await _store(session)
            got = payload(
                await session.call_tool(
                    "credential_get",
                    {"service_name": "kraken-api", "fields": "api_token", "into": str(out)},
                )
            )
    assert got["ok"], got
    assert _SECRET not in json.dumps(got)
    assert out.read_text() == _SECRET
    if os.name == "posix":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600


@pytest.mark.parametrize("reveal", [True, "true", 1])
async def test_reveal_is_refused_so_no_inline_secret_can_be_relayed(brain, local_db, reveal):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            await _store(session)
            got = payload(
                await session.call_tool(
                    "credential_get", {"service_name": "kraken-api", "reveal": reveal}
                )
            )
            plain = payload(
                await session.call_tool("credential_get", {"service_name": "kraken-api"})
            )
    assert got["ok"] is False and "into" in got["error"]
    assert _SECRET not in json.dumps(got)
    assert plain["ok"]  # an explicit or default reveal=False still works


async def test_the_vault_answers_while_the_brain_is_down(brain, local_db):
    with anyio.fail_after(30):
        async with serving(brain.app()) as served:
            async with through_proxy(served.url, brain.machine, vault_db=local_db) as (
                session,
                _,
            ):
                await session.initialize()
                await session.list_tools()
                await _store(session)
                await served.stop()
                listed = payload(await session.call_tool("credential_list", {}))
    assert listed["ok"] and listed["count"] == 1


async def test_a_tool_error_comes_back_as_a_result_not_a_crash(brain, local_db):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            missing = payload(
                await session.call_tool("credential_get", {"service_name": "no-such"})
            )
            bad = await session.call_tool("credential_get", {})  # required arg missing
            assert payload(await session.call_tool("memory_stats", {}))["ok"]
    assert missing["ok"] is False and "not found" in missing["error"]
    assert bad.isError


async def test_a_vault_that_cannot_open_ends_the_proxy_with_a_message(brain, tmp_path):
    from gingugu import proxy

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # the DB's parent is a file: mkdir fails
    with anyio.fail_after(15):
        async with through_proxy(
            "http://127.0.0.1:9", brain.machine, vault_db=blocker / "local.db"
        ) as (_, outcome):
            pass
    raised = outcome.get("raised")
    assert isinstance(raised, proxy.ProxyLost), outcome
    assert "MEMORY_CREDENTIALS_ENABLED=false" in str(raised)


async def _two_namespaces(session) -> None:
    for ns in ("alpha", "beta"):
        args = {"content": "x", "title": f"in {ns}", "type": "fact", "namespace": ns}
        assert payload(await session.call_tool("memory_store", args))["ok"]


async def test_memory_stats_reports_the_local_vault_not_the_brains(brain, local_db):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, vault_db=local_db) as (session, _):
            await session.initialize()
            await _store(session)
            await _two_namespaces(session)
            single = await session.call_tool("memory_stats", {})
            multi = await session.call_tool("memory_stats", {"namespace": "alpha,beta"})
    assert payload(single)["stats"]["credentials"]["total"] == 1
    assert payload(multi)["global"]["credentials"]["total"] == 1
    for result in (single, multi):
        assert json.dumps(result.structuredContent or {}).count('"total": 0') == 0


async def test_without_a_vault_memory_stats_passes_through(brain):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine) as (session, _):
            await session.initialize()
            got = payload(await session.call_tool("memory_stats", {}))
    assert got["ok"] and got["stats"]["credentials"]["total"] == 0  # the brain's own


def test_a_stats_reply_that_is_not_json_passes_through_untouched():
    from gingugu.proxy_vault import patch_stats

    health = {"total": 3, "expired": 0, "expiring_soon": 0}
    text = {"content": [{"type": "text", "text": "not json"}]}
    assert patch_stats(json.loads(json.dumps(text)), health) == text
    error = {"content": [{"type": "text", "text": '{"ok": false, "error": "nope"}'}]}
    assert patch_stats(json.loads(json.dumps(error)), health) == error


def test_a_patch_that_fails_part_way_relays_the_brains_reply_whole(capsys):
    from mcp.types import JSONRPCResponse

    from gingugu.proxy_session import Link

    class _Vault:
        def health(self):
            return {"total": 3, "expired": 0, "expiring_soon": 0}

    stats = json.dumps({"ok": True, "stats": {"credentials": {"total": 0}}})
    # A valid stats block, then an item patch_stats cannot read: it raises mid-way.
    result = {"content": [{"type": "text", "text": stats}, "not-a-block"]}
    msg = JSONRPCResponse(jsonrpc="2.0", id=1, result=json.loads(json.dumps(result)))
    Link(None, None, None, vault=_Vault())._patch_stats(msg)
    assert msg.result == result
    assert "not patched" in capsys.readouterr().err


async def test_without_a_vault_credentials_stay_hidden_and_refused(brain):
    # MEMORY_CREDENTIALS_ENABLED=false (personas, minions): unchanged from B1.
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine) as (session, _):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
    assert not (names & _CRED_TOOLS)
