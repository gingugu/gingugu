"""Named owner tokens, one per machine, and the SSH forced-command mint.

A machine token is full access, like the server's owner token, but named and
revocable on its own. It can only be created explicitly (``--owner`` or the
SSH mint); a ``*=write`` grant spec stays refused, and a tampered file cannot
turn a scoped entry into an owner one.
"""

from __future__ import annotations

import json
import sys

import pytest

from gingugu.grants import READ
from gingugu.serve_tokens import TokenStore
from gingugu.token_cli import main


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "serve_tokens.json")


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memories.db"))
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    return TokenStore(tmp_path / "serve_tokens.json")


# --- the store ---------------------------------------------------------------


def test_owner_token_resolves_to_full_access(store):
    token = store.add_owner("mbp-2")
    grant = store.resolve(token)
    assert grant is not None and grant.is_full
    assert grant.name == "mbp-2"


def test_owner_token_is_listed_as_owner(store):
    store.add_owner("mbp-2")
    store.add("reviewer", {"gingugu": READ})
    by_name = {e["name"]: e for e in store.list()}
    assert by_name["mbp-2"]["owner"] is True
    assert by_name["reviewer"]["owner"] is False


def test_owner_token_plaintext_is_never_written(store):
    token = store.add_owner("mbp-2")
    assert token not in store.path.read_text(encoding="utf-8")


def test_owner_duplicate_name_is_rejected_without_replace(store):
    store.add_owner("mbp-2")
    with pytest.raises(ValueError):
        store.add_owner("mbp-2")


def test_owner_replace_rotates_the_token(store):
    old = store.add_owner("mbp-2")
    new = store.add_owner("mbp-2", replace=True)
    assert old != new
    assert store.resolve(old) is None
    assert store.resolve(new).is_full
    assert [e["name"] for e in store.list()] == ["mbp-2"]


def test_owner_revoke_takes_effect(store):
    token = store.add_owner("mbp-2")
    assert store.revoke("mbp-2")
    assert store.resolve(token) is None


@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_owner_flag_must_be_literal_true(store, value):
    token = store.add("reviewer", {"gingugu": READ})
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["tokens"][0]["owner"] = value
    data["tokens"][0]["namespaces"] = {}
    store.path.write_text(json.dumps(data), encoding="utf-8")
    grant = store.resolve(token)
    assert grant is None or not grant.is_full


def test_wildcard_write_spec_is_still_refused(store):
    with pytest.raises(ValueError, match="owner"):
        store.add("sneaky", {"*": "write"})


# --- `gingugu token add --owner` ---------------------------------------------


def test_cli_add_owner_prints_token_once(cli_env, capsys):
    assert main(["add", "mbp-2", "--owner"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1].strip()
    assert cli_env.resolve(token).is_full


def test_cli_add_owner_and_ns_are_exclusive(cli_env, capsys):
    assert main(["add", "mbp-2", "--owner", "--ns", "gingugu=read"]) == 2
    assert cli_env.list() == []


def test_cli_add_needs_owner_or_ns(cli_env, capsys):
    assert main(["add", "mbp-2"]) == 2


def test_cli_list_marks_owner_tokens(cli_env, capsys):
    main(["add", "mbp-2", "--owner"])
    capsys.readouterr()
    assert main(["list"]) == 0
    line = [ln for ln in capsys.readouterr().out.splitlines() if "mbp-2" in ln][0]
    assert "owner" in line


# --- `gingugu token ssh-mint` (an authorized_keys forced command) ------------


def _mint(monkeypatch, capsys, original: str | None) -> tuple[int, str, str]:
    if original is None:
        monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    else:
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", original)
    code = main(["ssh-mint"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_ssh_mint_prints_only_an_owner_token(cli_env, monkeypatch, capsys):
    code, out, _ = _mint(monkeypatch, capsys, "mint mbp-2")
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 1
    grant = cli_env.resolve(lines[0])
    assert grant.is_full and grant.name == "mbp-2"


def test_ssh_mint_again_rotates(cli_env, monkeypatch, capsys):
    _, first, _ = _mint(monkeypatch, capsys, "mint mbp-2")
    _, second, _ = _mint(monkeypatch, capsys, "mint mbp-2")
    assert first.strip() != second.strip()
    assert cli_env.resolve(first.strip()) is None
    assert cli_env.resolve(second.strip()).is_full
    assert len(cli_env.list()) == 1


@pytest.mark.parametrize(
    "original",
    [
        None,
        "",
        "bash",
        "mint",
        "mint a b",
        "mint ../etc",
        "mint $(id)",
        "mint -rf",
        "mint " + "x" * 65,
        "revoke mbp-2",
        "list",
    ],
)
def test_ssh_mint_refuses_anything_else(cli_env, monkeypatch, capsys, original):
    code, out, _ = _mint(monkeypatch, capsys, original)
    assert code == 2
    assert out == ""
    assert cli_env.list() == []


def test_owner_replace_never_clobbers_a_scoped_token(store):
    scoped = store.add("reviewer", {"gingugu": READ})
    with pytest.raises(ValueError):
        store.add_owner("reviewer", replace=True)
    assert store.resolve(scoped) is not None
    assert [e["owner"] for e in store.list()] == [False]


def test_ssh_mint_cannot_turn_a_scoped_name_into_owner(cli_env, monkeypatch, capsys):
    scoped = cli_env.add("reviewer", {"gingugu": READ})
    code, out, _ = _mint(monkeypatch, capsys, "mint reviewer")
    assert code == 1 and out == ""
    assert cli_env.resolve(scoped) is not None


# --- a key bound to one machine name (`ssh-mint --name NAME`) ---------------
# Per-machine keys make revocation real: the key can only ever rotate its own
# machine's token, so deleting that key's authorized_keys line cuts the machine
# off without touching any other.


def _bound(monkeypatch, capsys, original: str | None, name: str = "mbp-2"):
    if original is None:
        monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    else:
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", original)
    code = main(["ssh-mint", "--name", name])
    captured = capsys.readouterr()
    return code, captured.out


@pytest.mark.parametrize("original", ["mint mbp-2", "mint", None, ""])
def test_bound_mint_mints_only_its_own_name(cli_env, monkeypatch, capsys, original):
    code, out = _bound(monkeypatch, capsys, original)
    assert code == 0
    grant = cli_env.resolve(out.strip())
    assert grant.is_full and grant.name == "mbp-2"


@pytest.mark.parametrize("original", ["mint other-box", "mint mbp-2 x", "bash", "revoke mbp-2"])
def test_bound_mint_refuses_any_other_name_or_verb(cli_env, monkeypatch, capsys, original):
    code, out = _bound(monkeypatch, capsys, original)
    assert code == 2 and out == ""
    assert cli_env.list() == []


def test_bound_name_itself_is_validated(cli_env, monkeypatch, capsys):
    code, out = _bound(monkeypatch, capsys, "mint", name="../x")
    assert code == 2 and out == ""


def test_top_level_dispatches_token_ssh_mint(cli_env, monkeypatch, capsys):
    from gingugu import server

    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "mint mbp-2")
    monkeypatch.setattr(sys, "argv", ["gingugu", "token", "ssh-mint"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert cli_env.list()[0]["owner"] is True
