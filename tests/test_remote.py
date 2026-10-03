"""`gingugu remote`: which brain this machine (or one client) talks to.

Local is the default and needs no setting at all, so a fresh clone or a fresh
install is never pointed at someone's server. A machine setting lives in the
user data dir, never in a repo. A client can override it for itself with
MEMORY_REMOTE_URL (``off`` forces local). The token comes from the OS keychain
and is minted over SSH by the server's forced command - it is never printed.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from gingugu import remote

URL = "http://brain.local:8765"
TOKEN = "A" * 43


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated machine setting + an in-memory keychain."""
    settings = tmp_path / "data" / "remote.json"
    monkeypatch.setattr(remote, "settings_path", lambda: settings)
    vault: dict[str, str] = {}
    monkeypatch.setattr(remote, "_kr_get", lambda url: vault.get(url))
    monkeypatch.setattr(remote, "_kr_set", lambda url, tok: vault.__setitem__(url, tok))
    monkeypatch.setattr(remote, "_kr_delete", lambda url: vault.pop(url, None))
    monkeypatch.delenv("MEMORY_REMOTE_URL", raising=False)
    return settings, vault


def _ok_runner(calls: list, stdout: str = TOKEN + "\n", code: int = 0):
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    return run


# --- which brain is active ---------------------------------------------------


def test_default_is_local(env):
    assert remote.active() is None


def test_machine_setting_makes_it_remote(env):
    env[1][URL] = TOKEN
    assert remote.main(["on", URL]) == 0
    target = remote.active()
    assert target.url == URL and target.source == "machine"


def test_env_overrides_machine_setting(env, monkeypatch):
    env[1][URL] = TOKEN
    remote.main(["on", URL])
    monkeypatch.setenv("MEMORY_REMOTE_URL", "http://other.local:8765/")
    target = remote.active()
    assert target.url == "http://other.local:8765" and target.source == "env"


def test_env_off_forces_local(env, monkeypatch):
    env[1][URL] = TOKEN
    remote.main(["on", URL])
    monkeypatch.setenv("MEMORY_REMOTE_URL", "off")
    assert remote.active() is None


def test_env_url_works_without_machine_setting(env, monkeypatch):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    assert remote.active().source == "env"
    assert not env[0].exists()


@pytest.mark.parametrize("bad", ["brain.local:8765", "ftp://x", "http://", "  "])
def test_bad_env_url_fails_loudly_never_silently_local(env, monkeypatch, bad):
    monkeypatch.setenv("MEMORY_REMOTE_URL", bad)
    with pytest.raises(ValueError):
        remote.active()


def test_token_for_reads_the_keychain(env):
    env[1][URL] = TOKEN
    assert remote.token_for(URL + "/") == TOKEN
    assert remote.token_for("http://nobody.local:1") is None


# --- on / off ----------------------------------------------------------------


def test_on_without_a_token_refuses_and_writes_nothing(env, capsys):
    assert remote.main(["on", URL]) == 1
    assert "login" in capsys.readouterr().err
    assert not env[0].exists()


def test_on_writes_an_owner_only_setting_outside_any_repo(env):
    env[1][URL] = TOKEN
    remote.main(["on", URL])
    assert env[0].exists()
    if sys.platform != "win32":
        assert env[0].stat().st_mode & 0o777 == 0o600
    assert TOKEN not in env[0].read_text(encoding="utf-8")


def test_off_goes_back_to_local(env):
    env[1][URL] = TOKEN
    remote.main(["on", URL])
    assert remote.main(["off"]) == 0
    assert remote.active() is None


def test_off_when_already_local_is_fine(env):
    assert remote.main(["off"]) == 0


# --- login: mint over SSH, straight into the keychain ------------------------


def test_login_mints_over_ssh_and_stores_in_keychain(env, monkeypatch, capsys):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ok_runner(calls))
    code = remote.main(["login", URL, "--ssh", "pi@brain.local", "--name", "mbp-2", "--key", "/k"])
    assert code == 0
    assert env[1][URL] == TOKEN
    argv = calls[0]
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv and "IdentitiesOnly=yes" in argv
    assert argv[argv.index("-i") + 1] == "/k"
    assert argv[argv.index("-F") + 1] == "/dev/null"
    assert argv[-3:] == ["pi@brain.local", "mint", "mbp-2"]
    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err


def test_login_does_not_switch_the_machine(env, monkeypatch):
    monkeypatch.setattr(remote, "_run", _ok_runner([]))
    remote.main(["login", URL, "--ssh", "pi@brain.local", "--name", "mbp-2"])
    assert remote.active() is None


def test_login_defaults_name_to_short_hostname_and_key_to_gingugu_pi(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ok_runner(calls))
    monkeypatch.setattr(remote.socket, "gethostname", lambda: "Work-Laptop.local")
    remote.main(["login", URL, "--ssh", "pi@brain.local"])
    argv = calls[0]
    assert argv[-1] == "work-laptop"
    assert argv[argv.index("-i") + 1].endswith("gingugu_mint")


@pytest.mark.parametrize(
    "stdout,code",
    [("", 0), ("not a token!\n", 0), (TOKEN + "\n", 255), ("a\nb\n", 0)],
)
def test_login_failure_stores_nothing(env, monkeypatch, capsys, stdout, code):
    monkeypatch.setattr(remote, "_run", _ok_runner([], stdout=stdout, code=code))
    assert remote.main(["login", URL, "--ssh", "pi@brain.local", "--name", "mbp-2"]) == 1
    assert env[1] == {}
    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err


@pytest.mark.parametrize("target", ["-oProxyCommand=x", "-F/tmp/c", "--"])
def test_login_rejects_an_ssh_target_that_is_an_option(env, monkeypatch, target):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ok_runner(calls))
    assert remote.main(["login", URL, f"--ssh={target}", "--name", "mbp-2"]) == 2
    assert calls == []


def test_login_reports_a_keychain_failure_clearly(env, monkeypatch, capsys):
    monkeypatch.setattr(remote, "_run", _ok_runner([]))

    def broken(url, tok):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(remote, "_kr_set", broken)
    assert remote.main(["login", URL, "--ssh", "pi@brain.local", "--name", "mbp-2"]) == 1
    err = capsys.readouterr().err
    assert "keychain" in err and "no keyring backend" in err and TOKEN not in err


def test_status_says_keychain_error_not_missing(env, monkeypatch, capsys):
    env[1][URL] = TOKEN
    remote.main(["on", URL])

    def broken(url):
        raise RuntimeError("locked")

    monkeypatch.setattr(remote, "_kr_get", broken)
    monkeypatch.setattr(remote, "_reachable", lambda url, token: True)
    capsys.readouterr()
    assert remote.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "keychain error" in out and "missing" not in out


def test_on_warns_about_plain_http_to_another_host(env, capsys):
    env[1][URL] = TOKEN
    remote.main(["on", URL])
    assert "unencrypted" in capsys.readouterr().err


def test_on_does_not_warn_for_https_or_loopback(env, capsys):
    for url in ("https://brain.example:8765", "http://127.0.0.1:8765", "http://localhost:8765"):
        env[1][url] = TOKEN
        remote.main(["on", url])
        assert "unencrypted" not in capsys.readouterr().err


def test_login_rejects_a_bad_name_before_running_ssh(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ok_runner(calls))
    assert remote.main(["login", URL, "--ssh", "pi@brain.local", "--name", "../x"]) == 2
    assert calls == []


# --- status ------------------------------------------------------------------


def test_status_local(env, capsys):
    assert remote.main(["status"]) == 0
    assert "local" in capsys.readouterr().out


def test_status_remote_never_prints_the_token(env, capsys, monkeypatch):
    env[1][URL] = TOKEN
    remote.main(["on", URL])
    monkeypatch.setattr(remote, "_reachable", lambda url, token: True)
    capsys.readouterr()
    assert remote.main(["status"]) == 0
    out = capsys.readouterr().out
    assert URL in out and "machine" in out and TOKEN not in out


@pytest.mark.parametrize("argv", [[], ["bogus"], ["on"], ["login", URL]])
def test_bad_usage_exits_2(env, argv):
    assert remote.main(argv) == 2


def test_top_level_dispatches_remote(env, monkeypatch, capsys):
    from gingugu import server

    monkeypatch.setattr(sys, "argv", ["gingugu", "remote", "status"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
