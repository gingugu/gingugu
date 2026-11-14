"""A crew mate's board mail: its own local brain, plus the crew brain it is linked to.

A crew mate runs a local brain and reaches the shared one only through a crew
link (``crew.json`` in the data dir, or ``MEMORY_CREW_URL``). Its prompt hook
shows mail from both: the local inbox as always, and the crew brain's
``/hook/inbox`` asked on the link's token. An unreachable crew brain costs
nothing - the local mail still arrives.
"""

from __future__ import annotations

import json

import pytest

from gingugu import crew
from tests.hook_remote_fixtures import URL, Brain
from tests.test_board_hook import _post, _run, local  # noqa: F401 - fixture

CREW_POST = {
    "id": "c1",
    "parent_id": None,
    "author": "beepboop",
    "namespace": "crew",
    "kind": "message",
    "body": "ahoy from the pi",
}


@pytest.fixture
def mate(local, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEMORY_PERSONA", "polly")
    monkeypatch.delenv("MEMORY_REMOTE_URL", raising=False)
    monkeypatch.setattr(crew, "settings_path", lambda: local_dir(local) / "crew.json")

    def install(replies: dict) -> Brain:
        from gingugu import hook_remote

        brain = Brain(replies)
        monkeypatch.setattr(hook_remote, "post", brain)
        return brain

    return local, install


def local_dir(conn):
    return __import__("pathlib").Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def test_crew_mail_arrives_beside_local_mail(mate, monkeypatch):
    conn, install = mate
    monkeypatch.setenv("MEMORY_CREW_URL", URL)
    _post(conn, recipient="polly", body="from my own brain")
    brain = install({"/hook/inbox": {"posts": [CREW_POST]}})
    out = _run("go")["additionalContext"]
    assert "from my own brain" in out and "ahoy from the pi" in out
    body = next(b for p, b in brain.calls if p == "/hook/inbox")
    assert body["persona"] == "polly" and body["namespaces"] is None


def test_crew_mail_is_shown_once(mate, monkeypatch):
    _conn, install = mate
    monkeypatch.setenv("MEMORY_CREW_URL", URL)
    brain = install({"/hook/inbox": {"posts": [CREW_POST]}})
    assert "ahoy from the pi" in _run("go")["additionalContext"]
    _run("go")
    assert [b for p, b in brain.calls if p == "/hook/inbox"][-1]["shown"] == ["c1"]


def test_an_unreachable_crew_brain_still_shows_local_mail(mate, monkeypatch):
    conn, install = mate
    monkeypatch.setenv("MEMORY_CREW_URL", URL)
    _post(conn, recipient="polly", body="from my own brain")
    install({"/hook/inbox": None})
    assert "from my own brain" in _run("go")["additionalContext"]


def test_no_crew_link_asks_no_crew_brain(mate):
    conn, install = mate
    _post(conn, recipient="polly", body="from my own brain")
    brain = install({})
    assert "from my own brain" in _run("go")["additionalContext"]
    assert brain.calls == []


def test_the_machine_setting_links_a_crew_brain(mate, monkeypatch):
    conn, install = mate
    monkeypatch.delenv("MEMORY_CREW_URL", raising=False)
    (local_dir(conn) / "crew.json").write_text(json.dumps({"url": URL}))
    brain = install({"/hook/inbox": {"posts": [CREW_POST]}})
    assert "ahoy from the pi" in _run("go")["additionalContext"]
    assert brain.paths() == ["/hook/inbox"]


def test_crew_url_off_unlinks(mate, monkeypatch):
    conn, install = mate
    (local_dir(conn) / "crew.json").write_text(json.dumps({"url": URL}))
    monkeypatch.setenv("MEMORY_CREW_URL", "off")
    assert crew.active() is None
