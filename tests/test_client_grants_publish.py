"""The server's half of the client-grant fence: publishing, withdrawal, the
startup race, and a real client -> (server, hook) process tree. See
``test_client_grants.py`` for what a hook makes of a publication."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import psutil
import pytest

from gingugu import client_grants, hook_grant
from tests.client_grants_fixtures import (
    CLIENT,
    HOST,
    _hook_under,
    _links,
    _Proc,
    _publication,
)

# --- publishing ---------------------------------------------------------------


def test_publish_writes_the_grant_and_the_servers_chain(root, monkeypatch) -> None:
    launcher = _Proc(500, others=False)
    _hook_under(monkeypatch, launcher, CLIENT, HOST)
    path = client_grants.publish("tyrone=write,crow=read")
    assert path == root / f"{os.getpid()}.json"
    body = json.loads(path.read_text())
    assert (body["grant"], body["role"]) == ("tyrone=write,crow=read", None)
    assert body["chain"] == _links(launcher, CLIENT, HOST)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_a_minion_server_says_so(root, monkeypatch) -> None:
    monkeypatch.setenv(client_grants.ROLE_ENV, "minion")
    path = client_grants.publish("gingugu=read,minions=write")
    assert json.loads(path.read_text())["role"] == "minion"


def test_publish_never_raises(monkeypatch, tmp_path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("")  # a directory under a regular file fails on every OS
    monkeypatch.setattr(client_grants, "_root", lambda: blocker / "clients")
    assert client_grants.publish("crow=read") is None


def test_release_withdraws(root) -> None:
    path = client_grants.publish(None)
    client_grants.release(path)
    assert not path.exists()


def test_publish_sweeps_dead_publications_but_keeps_a_live_clients_fence(root) -> None:
    me = [os.getpid(), round(psutil.Process().create_time(), 2)]
    roots = [[1, 0.0], [0, 0.0]]
    for name, grant, chain in [
        ("999999991", None, [me, *roots]),  # dead, unfenced: gone
        ("999999992", "crow=read", [[999999990, 1.0], *roots]),  # dead fence, dead tree: gone
        ("999999993", "crow=read", [me, *roots]),  # dead fence, its client lives: kept
        ("999999994", "crow=read", [me, [1, 0.0]]),  # client right under pid 1: kept
    ]:
        body = {"grant": grant, "role": None, "created": 1.0, "chain": chain}
        (root / f"{name}.json").write_text(json.dumps(body))
    client_grants.publish(None)
    assert sorted(p.stem for p in root.glob("99999999*.json")) == ["999999993", "999999994"]


# --- the server's side --------------------------------------------------------


def _main(monkeypatch, run=lambda: None) -> None:
    import gingugu.server as srv

    class _Server:
        def run(self) -> None:
            run()

    monkeypatch.setattr(sys, "argv", ["gingugu"])
    monkeypatch.setattr(srv, "build_server", lambda: _Server())
    srv.main()


def test_the_stdio_server_publishes_before_it_serves_and_withdraws_after(root, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_GRANT", "crow=read")
    seen: dict = {}

    def run() -> None:
        seen["files"] = [json.loads(p.read_text())["grant"] for p in root.glob("*.json")]

    _main(monkeypatch, run)
    assert seen["files"] == ["crow=read"]
    assert not list(root.glob("*.json"))


def test_a_server_that_refuses_its_grant_leaves_its_fence(root, monkeypatch, tmp_path) -> None:
    import gingugu.server as srv

    monkeypatch.setattr(sys, "argv", ["gingugu"])
    monkeypatch.setenv("MEMORY_GRANT", "*=write")
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    with pytest.raises(SystemExit):
        srv.main()
    assert [json.loads(p.read_text())["grant"] for p in root.glob("*.json")] == ["*=write"]


# --- the startup race ---------------------------------------------------------


def test_a_young_client_is_waited_on_for_the_whole_window(root, monkeypatch, live) -> None:
    young = _Proc(4343, age=0.2)
    _hook_under(monkeypatch, young, HOST)
    waited: list[float] = []

    def fake_sleep(s: float) -> None:
        waited.append(s)  # both servers publish while the hook waits
        live.add(_publication(root, 900, None, via=(young, HOST)))
        live.add(_publication(root, 901, "crow=read", via=(young, HOST)))

    monkeypatch.setattr(client_grants.time, "sleep", fake_sleep)
    with pytest.raises(ValueError, match="disagree"):
        hook_grant.client_grant()
    assert 2.5 < waited[0] <= client_grants.STARTUP_WINDOW_S


def test_an_old_client_does_not_wait(root, hook, monkeypatch) -> None:
    monkeypatch.setattr(client_grants.time, "sleep", lambda _s: pytest.fail("waited"))
    assert hook_grant.client_grant() is None


# --- the real process tree ----------------------------------------------------

_CLIENT = textwrap.dedent("""
    import subprocess, sys
    server_code, hook_code, ready = sys.argv[1], sys.argv[2], sys.argv[3]
    server = subprocess.Popen([sys.executable, "-c", server_code, ready])
    hook = subprocess.run([sys.executable, "-c", hook_code], capture_output=True, text=True)
    server.terminate()
    server.wait()
    print(hook.stdout.strip())
    """)

_SERVER = textwrap.dedent("""
    import os, sys, time
    from pathlib import Path
    from gingugu import client_grants
    client_grants._root = lambda: Path(os.environ["GG_ROOT"])
    client_grants.publish("tyrone=write,crow=read")
    Path(sys.argv[1]).touch()
    time.sleep(30)
    """)

_HOOK = textwrap.dedent("""
    import os
    from pathlib import Path
    from gingugu import client_grants, hook_grant
    client_grants._root = lambda: Path(os.environ["GG_ROOT"])
    g = hook_grant.client_grant()
    pairs = [] if g is None else sorted(f"{k}={v}" for k, v in g.namespaces.items())
    print(",".join(pairs) or "unfenced")
    """)


def test_a_real_client_tree(tmp_path) -> None:
    # Client -> (server, hook) as real processes: the hook finds the client by
    # its other child, waits out the young-client window, and reads the grant.
    env = {k: v for k, v in os.environ.items() if k != "MEMORY_GRANT"}
    env["GG_ROOT"] = str(tmp_path / "clients")
    out = subprocess.run(
        [sys.executable, "-c", _CLIENT, _SERVER, _HOOK, str(tmp_path / "ready")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert out.stdout.strip() == "crow=read,tyrone=write", out.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
def test_a_signal_withdraws_and_still_kills(tmp_path, sig) -> None:
    import signal

    code = textwrap.dedent("""
        import os, sys, time
        from pathlib import Path
        from gingugu import client_grants
        client_grants._root = lambda: Path(sys.argv[1])
        path = client_grants.publish("crow=read")
        client_grants.release_on_signal(path)
        print(path, flush=True)
        time.sleep(30)
        """)
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    path = Path(proc.stdout.readline().strip())
    assert path.exists()
    proc.send_signal(getattr(signal, sig))
    assert proc.wait(timeout=10) == -getattr(signal, sig)  # died of it, as before
    assert not path.exists()
