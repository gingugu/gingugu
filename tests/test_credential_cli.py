"""`gingugu credential`: the vault by hand, for a person at a terminal.

The way out when no client can show the confirmation dialog. Typing the name
back at a real terminal is the confirmation, so a delete refuses a stdin that is
not a TTY - a pipe is how an agent with a shell would answer for the person.
"""

from __future__ import annotations

import io

import keyring
import pytest

from gingugu import credential_cli, server
from gingugu.credentials import CredentialVault
from gingugu.database import Database


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "vault.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(path))
    monkeypatch.delenv("MEMORY_CREDENTIALS_ENABLED", raising=False)
    vault = CredentialVault(Database(path).connect())
    vault.store(
        service_name="kraken-api",
        fields={
            "api_token": {"value": "sk-kraken-secret", "is_secret": True},
            "region": {"value": "us-east-1", "is_secret": False},
        },
    )
    return path


def _names(path) -> list[str]:
    return [e["service_name"] for e in CredentialVault(Database(path).connect()).list(False)]


def _typed(monkeypatch, text: str | None) -> None:
    """``text`` typed at a terminal; None is a pipe already holding the right answer."""
    stdin = _Tty(text) if text is not None else io.StringIO("kraken-api\n")
    monkeypatch.setattr("sys.stdin", stdin)


def test_typing_the_name_back_deletes_the_service_and_its_secrets(db, monkeypatch, capsys):
    _typed(monkeypatch, "kraken-api\n")
    assert credential_cli.main(["delete", "kraken-api"]) == 0
    assert _names(db) == []
    assert keyring.get_password("gingugu", "kraken-api/api_token") is None
    assert "Deleted" in capsys.readouterr().out


@pytest.mark.parametrize("typed", ["kraken\n", "\n", "KRAKEN-API\n", ""])
def test_anything_else_typed_keeps_it(db, monkeypatch, typed):
    _typed(monkeypatch, typed)
    assert credential_cli.main(["delete", "kraken-api"]) == 1
    assert _names(db) == ["kraken-api"]


def test_a_piped_stdin_is_refused_before_anything_is_asked(db, monkeypatch, capsys):
    _typed(monkeypatch, None)  # a pipe, already holding the right answer
    assert credential_cli.main(["delete", "kraken-api"]) == 2
    assert _names(db) == ["kraken-api"]
    captured = capsys.readouterr()
    assert "terminal" in captured.err and "Type" not in captured.out


def test_a_missing_service_is_not_found_and_nobody_is_asked(db, monkeypatch, capsys):
    _typed(monkeypatch, "nope\n")
    assert credential_cli.main(["delete", "nope"]) == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err and "Type" not in captured.out


def test_one_field_can_go_on_its_own(db, monkeypatch):
    _typed(monkeypatch, "kraken-api\n")
    assert credential_cli.main(["delete", "kraken-api", "--field", "api_token"]) == 0
    left = CredentialVault(Database(db).connect()).list(False)
    assert left[0]["secret_field_names"] == [] and left[0]["non_secret_fields"] == {
        "region": "us-east-1"
    }


def test_a_missing_field_is_not_found(db, monkeypatch, capsys):
    _typed(monkeypatch, "kraken-api\n")
    assert credential_cli.main(["delete", "kraken-api", "--field", "nope"]) == 1
    assert "not found" in capsys.readouterr().err
    assert _names(db) == ["kraken-api"]


def test_list_shows_names_never_secret_values(db, capsys):
    assert credential_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "kraken-api" in out and "api_token" in out
    assert "sk-kraken-secret" not in out


def test_list_on_an_empty_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "empty.db"))
    assert credential_cli.main(["list"]) == 0
    assert "no credentials" in capsys.readouterr().out


def test_with_credentials_off_nothing_is_touched(db, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")
    _typed(monkeypatch, "kraken-api\n")
    assert credential_cli.main(["delete", "kraken-api"]) == 2
    assert "MEMORY_CREDENTIALS_ENABLED" in capsys.readouterr().err
    assert _names(db) == ["kraken-api"]


@pytest.mark.parametrize("argv", [[], ["bogus"], ["delete"], ["delete", "a", "--yes"]])
def test_usage_errors_exit_2(db, argv, capsys):
    assert credential_cli.main(argv) == 2
    assert "Usage:" in capsys.readouterr().err


def test_help(capsys):
    assert credential_cli.main(["--help"]) == 0
    assert "gingugu credential delete" in capsys.readouterr().out


def test_the_console_entry_point_dispatches_it(db, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["gingugu", "credential", "list"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert "kraken-api" in capsys.readouterr().out


def test_top_level_help_lists_it(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["gingugu", "--help"])
    server.main()
    assert "gingugu credential" in capsys.readouterr().out


def test_a_locked_keychain_is_a_message_not_a_traceback(db, monkeypatch, capsys):
    def locked(*_a, **_k):
        raise keyring.errors.KeyringError("locked")

    monkeypatch.setattr(keyring, "delete_password", locked)
    _typed(monkeypatch, "kraken-api\n")
    assert credential_cli.main(["delete", "kraken-api"]) == 1
    assert "keychain refused" in capsys.readouterr().err
    assert _names(db) == ["kraken-api"]


def test_a_planted_name_cannot_rewrite_the_terminal(db, capsys):
    vault = CredentialVault(Database(db).connect())
    vault.store(service_name="evil\x1b[2K\rkraken-api", fields={"t": {"value": "x"}})
    assert credential_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
