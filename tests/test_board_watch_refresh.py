"""A remote ``board watch`` re-reads the inbox on a timer, not only on events.

A routed reply becomes visible to a persona's other sessions when its grace
runs out, and nothing is written at that moment, so no resource update is sent.
A watch that refetched only on events would sit on that mail until some other
post happened to wake it.
"""

from __future__ import annotations

import io
import json

import anyio

from gingugu import board, board_watch, board_watch_remote
from gingugu.board_watch_remote import watch_remote
from gingugu.database import Database
from gingugu.namespaces import NamespaceManager
from tests.proxy_fixtures import make_brain, serving


def _write_silently(tmp_path, body: str) -> None:
    """A post straight into the brain's store: no tool call, so no event."""
    from gingugu.config import load_config

    database = Database(tmp_path / "brain.db")
    conn = database.connect()
    ns = NamespaceManager(conn, load_config()).get_or_create("crow")
    board.post(conn, namespace_id=ns.id, author="beepboop", recipient="tyrone", body=body)
    conn.commit()
    database.close()


async def test_an_idle_watch_finds_mail_that_arrived_without_an_event(tmp_path, monkeypatch):
    monkeypatch.setattr(board_watch_remote, "REFRESH_S", 0.2)
    brain = make_brain(tmp_path, monkeypatch)
    async with serving(brain.app()) as served:
        out = io.StringIO()
        ready = anyio.Event()

        async def watcher():
            await watch_remote(
                served.url,
                brain.machine,
                "tyrone",
                board_watch.Emitter(out),
                once=True,
                on_ready=ready.set,
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(watcher)
            with anyio.fail_after(10):
                await ready.wait()
            _write_silently(tmp_path, "no event for me")
            with anyio.fail_after(10):
                while not out.getvalue():
                    await anyio.sleep(0.05)
        lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
        assert [line["body"] for line in lines] == ["no event for me"]


def test_the_refresh_is_bounded_well_under_the_grace():
    assert 0 < board_watch_remote.REFRESH_S <= board.ROUTE_GRACE_SECONDS / 4
