"""``gingugu crew``: bring a crew mate aboard, and see one off.

Brain side (run as the brain's service user): ``crew add NAME`` creates the
mate's scoped token (``crew=write`` by default) and prints the authorized_keys
line for its key; ``crew remove NAME`` revokes it, which ends every token it
derived at once; ``crew list`` names the crew. A crew key's forced command is
``ssh-mint --crew``: it only ever rotates that mate's existing scoped token -
never mints an owner token, even after the mate was removed.

Mate side: ``crew join URL --ssh USER@HOST --name NAME`` makes a crew key
(first run: prints it for the captain), mints over SSH into the keychain,
links the crew brain, and prints the MCP server to add. ``crew leave`` unlinks
and forgets the token.
"""

from __future__ import annotations

import subprocess

import pytest

from gingugu import crew, remote
from gingugu.crew_cli import main as crew_main
from gingugu.serve_tokens import TokenStore
from gingugu.token_cli import main as token_main

URL = "http://127.0.0.1:8765"
TOKEN = "C" * 43


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memories.db"))
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    return TokenStore(tmp_path / "serve_tokens.json")


def _crew_mint(monkeypatch, capsys, name="polly", original="mint polly"):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", original)
    code = token_main(["ssh-mint", "--name", name, "--crew"])
    out = capsys.readouterr()
    return code, out.out.strip(), out.err


# --- brain side ----------------------------------------------------------------


def test_crew_add_makes_a_crew_token_and_prints_the_key_line(brain, capsys):
    assert crew_main(["add", "polly"]) == 0
    out = capsys.readouterr().out
    assert 'command="/usr/local/bin/gingugu-mint-crew polly"' in out and "restrict" in out
    assert brain.grant_named("polly").namespaces == {"crew": "write"}
    assert "C" * 43 not in out  # the token itself is never shown: the mate mints its own


def test_crew_add_twice_is_refused(brain, capsys):
    assert crew_main(["add", "polly"]) == 0
    assert crew_main(["add", "polly"]) == 1


@pytest.mark.parametrize("name", ["derived:polly", "bad name", "-x"])
def test_crew_add_refuses_a_bad_name(brain, name):
    assert crew_main(["add", name]) != 0


def test_crew_list_names_only_the_crew(brain, capsys):
    brain.add_owner("macbookpro")
    crew_main(["add", "polly"])
    capsys.readouterr()
    assert crew_main(["list"]) == 0
    out = capsys.readouterr().out
    assert "polly" in out and "macbookpro" not in out


def test_crew_remove_revokes(brain, capsys):
    crew_main(["add", "polly"])
    assert crew_main(["remove", "polly"]) == 0
    assert brain.grant_named("polly") is None
    assert "authorized_keys" in capsys.readouterr().out  # says to drop the key line
    assert crew_main(["remove", "polly"]) == 1


def test_crew_mint_rotates_and_keeps_the_grant(brain, monkeypatch, capsys):
    crew_main(["add", "polly"])
    capsys.readouterr()
    code, first, _ = _crew_mint(monkeypatch, capsys)
    assert code == 0 and brain.resolve(first).namespaces == {"crew": "write"}
    code, second, _ = _crew_mint(monkeypatch, capsys)
    assert code == 0 and brain.resolve(first) is None
    assert brain.resolve(second).namespaces == {"crew": "write"}


def test_a_crew_key_never_mints_an_owner_token(brain, monkeypatch, capsys):
    code, out, err = _crew_mint(monkeypatch, capsys)  # removed, or never added
    assert code != 0 and out == "" and "crew" in err
    assert brain.list() == []


def test_a_crew_key_cannot_mint_an_owner_machine(brain, monkeypatch, capsys):
    brain.add_owner("polly")
    code, out, _ = _crew_mint(monkeypatch, capsys)
    assert code != 0 and out == ""


def test_a_crew_key_still_hands_out_the_cert_only_on_cert(brain, monkeypatch, capsys):
    crew_main(["add", "polly"])
    capsys.readouterr()
    code, out, _ = _crew_mint(monkeypatch, capsys, original="mint tyrone")
    assert code == 2 and out == ""


# --- mate side -----------------------------------------------------------------


@pytest.fixture
def mate(tmp_path, monkeypatch):
    monkeypatch.setattr(crew, "settings_path", lambda: tmp_path / "data" / "crew.json")
    monkeypatch.setattr(remote, "settings_path", lambda: tmp_path / "data" / "remote.json")
    vault: dict[str, str] = {}
    monkeypatch.setattr(remote, "_kr_get", lambda url: vault.get(url))
    monkeypatch.setattr(remote, "_kr_set", lambda url, tok: vault.__setitem__(url, tok))
    monkeypatch.setattr(remote, "_kr_delete", lambda url: vault.pop(url, None))
    for key in ("MEMORY_CREW_URL", "MEMORY_REMOTE_URL"):
        monkeypatch.delenv(key, raising=False)
    key = tmp_path / "ssh" / "gingugu_crew"
    return vault, key


def _ssh(calls, *, minted=True):
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "ssh-keygen":
            path = argv[argv.index("-f") + 1]
            open(path, "w").close()
            with open(path + ".pub", "w") as fh:
                fh.write("ssh-ed25519 AAAAC3Nza polly@laptop\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[-1] == "cert":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if minted:
            return subprocess.CompletedProcess(argv, 0, TOKEN + "\n", "")
        return subprocess.CompletedProcess(argv, 255, "", "Permission denied (publickey).")

    return run


def _join(key, name="polly"):
    return crew_main(["join", URL, "--ssh", "cap@brain", "--name", name, "--key", str(key)])


def test_first_join_makes_a_key_and_asks_for_the_captain(mate, monkeypatch, capsys):
    vault, key = mate
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ssh(calls, minted=False))
    monkeypatch.setattr("gingugu.crew_cli._run", _ssh(calls, minted=False))
    assert _join(key) == 3  # waiting on the captain, not a failure
    out = capsys.readouterr().out
    assert "ssh-ed25519 AAAAC3Nza" in out and "gingugu crew add polly" in out
    assert vault == {} and crew.active() is None


def test_join_mints_links_and_prints_the_crew_server(mate, monkeypatch, capsys):
    vault, key = mate
    key.parent.mkdir()
    key.write_text("")
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ssh(calls))
    assert _join(key) == 0
    assert vault[URL] == TOKEN and crew.active().url == URL
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "claude mcp add --scope user crew" in out
    assert f"MEMORY_REMOTE_URL={URL}" in out and "MEMORY_NAMESPACE=crew" in out
    assert "MEMORY_PERSONA=polly" in out and "MEMORY_GRANT" not in out


def test_join_refuses_a_bad_name_before_ssh(mate, monkeypatch):
    _vault, key = mate
    calls: list = []
    monkeypatch.setattr(remote, "_run", _ssh(calls))
    assert _join(key, name="no good") == 2 and calls == []


def test_join_refuses_on_a_remote_machine(mate, monkeypatch):
    _vault, key = mate
    monkeypatch.setenv("MEMORY_REMOTE_URL", "https://pi.test")
    assert _join(key) == 2


def test_leave_unlinks_and_forgets_the_token(mate, monkeypatch, capsys):
    vault, key = mate
    key.parent.mkdir()
    key.write_text("")
    monkeypatch.setattr(remote, "_run", _ssh([]))
    _join(key)
    capsys.readouterr()
    assert crew_main(["leave"]) == 0
    assert crew.active() is None and vault == {}
    assert "claude mcp remove crew" in capsys.readouterr().out
