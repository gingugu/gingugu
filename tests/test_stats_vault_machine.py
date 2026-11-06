"""memory_stats' credential count names whose vault it is.

The vault is per machine, so a bare ``total: 0`` on a machine that never
vaulted anything reads as data loss. The block carries ``machine`` always, and
a ``note`` when the count is zero, in local mode and through the remote proxy.
"""

from __future__ import annotations

import json

from gingugu import machine, remote, stats
from gingugu.credentials import CredentialVault
from gingugu.proxy_vault import patch_stats
from gingugu.storage import MemoryStore


def _vault_one(store: MemoryStore) -> None:
    CredentialVault(store.conn).store(
        service_name="kraken-api", fields={"region": {"value": "us", "is_secret": False}}
    )


def test_machine_name_is_the_sanitised_short_hostname(monkeypatch) -> None:
    monkeypatch.setattr(machine.socket, "gethostname", lambda: "Mr-Boomtastic-2.local")
    assert machine.name() == "mr-boomtastic-2"
    monkeypatch.setattr(machine.socket, "gethostname", lambda: "Odd Box!.lan")
    assert machine.name() == "odd-box-"


def test_remote_login_defaults_to_the_same_name(monkeypatch) -> None:
    monkeypatch.setattr(machine.socket, "gethostname", lambda: "Kraken.local")
    assert remote._default_name() == machine.name() == "kraken"


def test_an_empty_vault_names_the_machine_and_says_zero_is_expected(store) -> None:
    creds = stats.compute_stats(store.conn)["credentials"]
    assert creds["total"] == 0
    assert creds["machine"] == machine.name()
    assert "per machine" in creds["note"] and "not data loss" in creds["note"]


def test_a_nonempty_vault_names_the_machine_without_a_note(store) -> None:
    _vault_one(store)
    creds = stats.compute_stats(store.conn)["credentials"]
    assert creds["total"] == 1
    assert creds["machine"] == machine.name()
    assert "note" not in creds


def test_the_comma_list_global_block_carries_the_same_fields(store) -> None:
    creds = stats.compute_global_stats(store.conn)["credentials"]
    assert creds["machine"] == machine.name() and "note" in creds


def test_describe_is_pure_and_leaves_its_input_alone() -> None:
    health = {"total": 0, "expired": 0, "expiring_soon": 0}
    out = machine.describe(health)
    assert "machine" not in health and out is not health
    assert machine.describe({**health, "total": 2}).get("note") is None


def _reply(shape: str) -> dict:
    brain = {"total": 0, "expired": 0, "expiring_soon": 0, "machine": "beepboop"}
    holder = {"credentials": brain, "namespaces": []}
    data = {"ok": True, shape: holder}
    return {"content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data}


def test_the_proxy_swaps_in_this_machines_named_vault_in_both_shapes() -> None:
    health = machine.describe({"total": 0, "expired": 0, "expiring_soon": 0})
    for shape in ("stats", "global"):
        got = patch_stats(_reply(shape), health)
        text = json.loads(got["content"][0]["text"])[shape]["credentials"]
        assert text["machine"] == machine.name()  # not the brain's "beepboop"
        assert "per machine" in text["note"]
        assert got["structuredContent"][shape]["credentials"] == text
