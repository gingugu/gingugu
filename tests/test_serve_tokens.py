"""Tests for the scoped serve-token store and the `gingugu token` CLI."""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from gingugu.grants import READ, WRITE, Grant
from gingugu.serve_tokens import TokenStore, main, parse_grant_spec

# --- grant spec parsing ------------------------------------------------------


def test_parse_grant_spec_maps_names_to_levels():
    assert parse_grant_spec("gingugu=read, scratch=write") == {
        "gingugu": READ,
        "scratch": WRITE,
    }


def test_parse_grant_spec_accepts_wildcard():
    assert parse_grant_spec("*=read,scratch=write") == {"*": READ, "scratch": WRITE}


@pytest.mark.parametrize(
    "spec",
    ["", "gingugu", "gingugu=admin", "=read", "gingugu=read,gingugu=write", " , "],
)
def test_parse_grant_spec_rejects_bad_input(spec):
    with pytest.raises(ValueError):
        parse_grant_spec(spec)


# --- the store ---------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "serve_tokens.json")


def test_add_returns_a_token_that_resolves_to_its_grant(store):
    token = store.add("laptop-2", {"gingugu": WRITE, "crow": READ})
    assert isinstance(token, str) and len(token) >= 32
    grant = store.resolve(token)
    assert grant == Grant("laptop-2", {"gingugu": WRITE, "crow": READ})


def test_tokens_are_unique(store):
    assert store.add("a", {"x": READ}) != store.add("b", {"x": READ})


def test_plaintext_token_is_never_written(store):
    token = store.add("laptop-2", {"gingugu": READ})
    text = store.path.read_text(encoding="utf-8")
    assert token not in text
    entry = json.loads(text)["tokens"][0]
    assert entry["name"] == "laptop-2"
    assert len(entry["sha256"]) == 64


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_file_is_owner_only(store):
    store.add("laptop-2", {"gingugu": READ})
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_unknown_token_resolves_to_none(store):
    store.add("laptop-2", {"gingugu": READ})
    assert store.resolve("not-a-token") is None
    assert store.resolve("") is None


def test_missing_file_resolves_to_none(store):
    assert not store.path.exists()
    assert store.resolve("anything") is None


def test_duplicate_name_is_rejected(store):
    store.add("laptop-2", {"gingugu": READ})
    with pytest.raises(ValueError):
        store.add("laptop-2", {"gingugu": WRITE})


def test_invalid_grant_is_rejected_and_nothing_written(store):
    with pytest.raises(ValueError):
        store.add("bad", {"gingugu": "admin"})
    with pytest.raises(ValueError):
        store.add("empty", {})
    assert store.list() == []


def test_list_shows_names_and_grants_never_hashes(store):
    store.add("laptop-2", {"gingugu": WRITE})
    store.add("minion", {"gingugu": READ, "scratch": WRITE})
    listed = store.list()
    assert [e["name"] for e in listed] == ["laptop-2", "minion"]
    assert listed[1]["namespaces"] == {"gingugu": READ, "scratch": WRITE}
    assert all("sha256" not in e for e in listed)
    assert all("created_at" in e for e in listed)


def test_revoke_removes_the_token(store):
    token = store.add("laptop-2", {"gingugu": READ})
    assert store.revoke("laptop-2") is True
    assert store.resolve(token) is None
    assert store.revoke("laptop-2") is False


def test_revoke_in_another_process_takes_effect_without_restart(store, tmp_path):
    token = store.add("laptop-2", {"gingugu": READ})
    assert store.resolve(token) is not None  # warm the server's cache
    TokenStore(store.path).revoke("laptop-2")  # the CLI, a separate instance
    assert store.resolve(token) is None


def test_add_in_another_process_is_picked_up(store):
    store.add("first", {"gingugu": READ})
    store.resolve("warm-the-cache")
    token = TokenStore(store.path).add("second", {"gingugu": READ})
    assert store.resolve(token) == Grant("second", {"gingugu": READ})


def test_corrupt_file_fails_closed(store):
    store.path.write_text("{not json", encoding="utf-8")
    assert store.resolve("anything") is None
    with pytest.raises(ValueError):
        store.add("laptop-2", {"gingugu": READ})


def test_entry_with_invalid_grant_never_resolves(store):
    token = store.add("laptop-2", {"gingugu": READ})
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["tokens"][0]["namespaces"] = {"gingugu": "admin"}
    store.path.write_text(json.dumps(data), encoding="utf-8")
    assert store.resolve(token) is None


# --- the CLI -----------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memories.db"))
    return tmp_path / "serve_tokens.json"


def test_cli_add_prints_token_once_and_stores_next_to_db(cli_env, capsys):
    assert main(["add", "laptop-2", "--ns", "gingugu=write,crow=read"]) == 0
    out = capsys.readouterr().out
    token = out.strip().splitlines()[-1].strip()
    assert TokenStore(cli_env).resolve(token) == Grant("laptop-2", {"gingugu": WRITE, "crow": READ})


def test_cli_list_and_revoke(cli_env, capsys):
    main(["add", "laptop-2", "--ns", "gingugu=read"])
    capsys.readouterr()
    assert main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "laptop-2" in listed and "gingugu=read" in listed
    assert main(["revoke", "laptop-2"]) == 0
    assert TokenStore(cli_env).list() == []


def test_cli_revoke_unknown_name_fails(cli_env, capsys):
    assert main(["revoke", "ghost"]) == 1


@pytest.mark.parametrize(
    "argv",
    [[], ["add"], ["add", "x"], ["add", "x", "--ns", "bad"], ["bogus"]],
)
def test_cli_rejects_bad_usage(cli_env, argv, capsys):
    assert main(argv) == 2


def test_cli_add_duplicate_fails(cli_env, capsys):
    main(["add", "laptop-2", "--ns", "gingugu=read"])
    assert main(["add", "laptop-2", "--ns", "gingugu=read"]) == 1


def test_cli_help_exits_zero(cli_env, capsys):
    assert main(["--help"]) == 0
    assert "gingugu token" in capsys.readouterr().out


def test_top_level_dispatches_token_subcommand(cli_env, monkeypatch, capsys):
    from gingugu import server

    monkeypatch.setattr(sys, "argv", ["gingugu", "token", "list"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
