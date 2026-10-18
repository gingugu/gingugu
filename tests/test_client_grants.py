"""A server's grant reaches its client's hooks (board #1, the fail-open fence).

The hooks run in the client's environment and the server in its MCP ``env``
block, so a ``MEMORY_GRANT`` set only in the latter used to fence the server
and leave involuntary recall, tripwires and warm-up unfenced, silently. Every
gingugu server now publishes the grant it runs under, keyed by its client's
process id, and ``hook_grant.client_grant`` applies what the client's servers
published. Disagreement anywhere refuses: the hook stays quiet and says why.
"""

from __future__ import annotations

import psutil
import pytest

from gingugu import client_grants, hook_grant
from tests.client_grants_fixtures import (
    CLIENT,
    HOST,
    _hook_under,
    _Proc,
    _publication,
)

# --- what a hook makes of it --------------------------------------------------


def test_nothing_published_falls_back_to_the_hooks_own_env(hook, monkeypatch) -> None:
    assert hook_grant.client_grant() is None
    monkeypatch.setenv("MEMORY_GRANT", "crow=read")
    assert dict(hook_grant.client_grant().namespaces) == {"crow": "read"}


def test_a_fenced_server_fences_a_hook_that_set_nothing(root, hook, live) -> None:
    live.add(_publication(root, 900, "tyrone=write,crow=read"))
    assert dict(hook_grant.client_grant().namespaces) == {"tyrone": "write", "crow": "read"}


def test_launchers_on_both_sides_meet_at_the_client(root, monkeypatch, live) -> None:
    # `uv run gingugu` for the server, `uv run hook.py` for the hook.
    _hook_under(monkeypatch, _Proc(600, others=False), CLIENT, HOST)
    live.add(_publication(root, 900, "crow=read", via=(_Proc(500), CLIENT, HOST)))
    assert dict(hook_grant.client_grant().namespaces) == {"crow": "read"}


def test_the_nearest_meeting_wins_over_another_session(root, hook, live) -> None:
    other_session = _Proc(777)
    live.add(_publication(root, 900, None))
    live.add(_publication(root, 901, "crow=read", via=(other_session, HOST)))
    assert hook_grant.client_grant() is None


def test_another_sessions_server_under_a_shared_host_is_not_ours(root, hook, live) -> None:
    # This session has no gingugu server; a fenced one runs in another session
    # under the same VS Code host. Its grant must not leak into these hooks.
    live.add(_publication(root, 901, "crow=read", via=(_Proc(777), HOST)))
    assert hook_grant.client_grant() is None


def test_a_reused_client_pid_does_not_meet(root, monkeypatch, live) -> None:
    _hook_under(monkeypatch, _Proc(CLIENT.pid, age=5.0), _Proc(2))
    live.add(_publication(root, 900, "crow=read"))
    assert hook_grant.client_grant() is None


def test_an_unfenced_server_leaves_the_hook_unfenced(root, hook, live) -> None:
    live.add(_publication(root, 900, None))
    assert hook_grant.client_grant() is None


def test_matching_grants_written_differently_agree(root, hook, live, monkeypatch) -> None:
    live.add(_publication(root, 900, "crow=read,tyrone=write"))
    monkeypatch.setenv("MEMORY_GRANT", "tyrone=write, crow=read")
    assert dict(hook_grant.client_grant().namespaces) == {"tyrone": "write", "crow": "read"}


@pytest.mark.parametrize("own", ["crow=read", "*=read"])
def test_the_hooks_own_grant_disagreeing_refuses(root, hook, live, monkeypatch, own) -> None:
    live.add(_publication(root, 900, "tyrone=write,crow=read"))
    monkeypatch.setenv("MEMORY_GRANT", own)
    with pytest.raises(ValueError, match="disagree"):
        hook_grant.client_grant()


def test_a_fenced_hook_under_an_unfenced_server_refuses(root, hook, live, monkeypatch) -> None:
    live.add(_publication(root, 900, None))
    monkeypatch.setenv("MEMORY_GRANT", "crow=read")
    with pytest.raises(ValueError, match="disagree"):
        hook_grant.client_grant()


def test_two_servers_on_different_grants_refuse(root, hook, live) -> None:
    live.add(_publication(root, 900, None))
    live.add(_publication(root, 901, "crow=read"))
    with pytest.raises(ValueError, match="disagree"):
        hook_grant.client_grant()


def test_a_minion_server_is_not_the_clients_grant(root, hook, live) -> None:
    live.add(_publication(root, 900, None))
    live.add(_publication(root, 901, "gingugu=read,minions=write", role="minion"))
    assert hook_grant.client_grant() is None


def test_a_minion_mark_alone_cannot_hide_a_fence(root, hook, live) -> None:
    # MEMORY_SERVER_ROLE inherited by the main server by accident.
    live.add(_publication(root, 900, "crow=read", role="minion"))
    assert dict(hook_grant.client_grant().namespaces) == {"crow": "read"}


def test_a_fenced_client_whose_server_is_gone_refuses(root, hook, live) -> None:
    # Crashed, lost its brain, or refused a bad grant at startup.
    _publication(root, 900, "crow=read")  # not live
    with pytest.raises(ValueError, match="fenced"):
        hook_grant.client_grant()


def test_a_dead_unfenced_server_is_as_if_nothing_was_published(root, hook, live) -> None:
    _publication(root, 900, None)  # not live
    assert hook_grant.client_grant() is None


def test_an_unreadable_publication_refuses(root, hook, live) -> None:
    (root / "900.json").write_text("{not json")
    with pytest.raises(ValueError):
        hook_grant.client_grant()


def test_a_publication_another_user_owns_refuses(root, hook, live, monkeypatch) -> None:
    live.add(_publication(root, 900, None))
    monkeypatch.setattr(client_grants, "_owned", lambda path: path.suffix != ".json")
    with pytest.raises(ValueError):
        hook_grant.client_grant()


def test_a_publication_dir_another_user_owns_refuses(root, hook, live, monkeypatch) -> None:
    live.add(_publication(root, 900, None))
    monkeypatch.setattr(client_grants, "_owned", lambda path: path != root)
    with pytest.raises(ValueError, match="not this user's"):
        hook_grant.client_grant()


def test_an_unreadable_process_tree_refuses(root, monkeypatch) -> None:
    def boom():
        raise psutil.AccessDenied(1)

    monkeypatch.setattr(client_grants, "ancestors", boom)
    with pytest.raises(ValueError, match="process tree"):
        hook_grant.client_grant()


@pytest.mark.parametrize("bad", ["", "*=write", "nonsense"])
def test_a_published_grant_that_cannot_be_honoured_refuses(root, hook, live, bad) -> None:
    live.add(_publication(root, 900, bad))
    with pytest.raises(ValueError):
        hook_grant.client_grant()


def test_a_refusal_quiets_the_hooks(root, hook, live) -> None:
    from gingugu.prompt_hook import namespaces_for

    live.add(_publication(root, 900, None))
    live.add(_publication(root, 901, "crow=read"))
    assert namespaces_for("/tmp/some-repo") == []
