"""The board watcher's delivery cursor when posts share a ``created_at``.

The inbox returns posts in ``(created_at, rowid)`` order - insertion order - but
ids are random UUIDs. A cursor that compares ``(created_at, id)`` treats a later
post with a smaller id at the same timestamp as already delivered and drops it.
Windows' coarse clock makes equal timestamps common; any platform can hit it.
"""

from __future__ import annotations

import io
import json

from gingugu.board_watch_emit import Cursor, Emitter

T = "2026-10-27T20:00:00.000000+00:00"


def _post(pid: str, at: str = T) -> dict:
    return {
        "id": pid,
        "author": "tyrone",
        "recipient": "beepboop",
        "kind": "message",
        "namespace": "alpha",
        "body": f"body {pid}",
        "created_at": at,
    }


def _ids(out: io.StringIO) -> list[str]:
    return [json.loads(line)["id"] for line in out.getvalue().splitlines() if line.strip()]


def test_posts_sharing_a_timestamp_all_emit_whatever_their_id_order():
    out = io.StringIO()
    # Insertion order deliberately the reverse of id order.
    assert Emitter(out, io.StringIO()).emit([_post("ff"), _post("aa"), _post("cc")]) == 3
    assert _ids(out) == ["ff", "aa", "cc"]


def test_a_tied_post_arriving_in_a_later_batch_is_not_lost():
    out = io.StringIO()
    emitter = Emitter(out, io.StringIO())
    emitter.emit([_post("ff")])
    assert emitter.emit([_post("ff"), _post("aa")]) == 1
    assert _ids(out) == ["ff", "aa"]


def test_a_reloaded_cursor_keeps_every_id_at_its_timestamp(tmp_path):
    path = tmp_path / "cursor.json"
    first = Emitter(io.StringIO(), io.StringIO(), cursor=Cursor(path))
    first.emit([_post("ff"), _post("aa")])

    out = io.StringIO()
    again = Emitter(out, io.StringIO(), cursor=Cursor(path))
    assert again.emit([_post("ff"), _post("aa"), _post("bb")]) == 1
    assert _ids(out) == ["bb"]


def test_a_newer_timestamp_resets_the_tied_set(tmp_path):
    path = tmp_path / "cursor.json"
    later = "2026-10-27T20:00:01.000000+00:00"
    emitter = Emitter(io.StringIO(), io.StringIO(), cursor=Cursor(path))
    emitter.emit([_post("aa"), _post("bb", later)])
    cursor = Cursor(path)
    assert cursor.passed(_post("aa"))  # older timestamp: delivered
    assert cursor.passed(_post("bb", later))
    assert not cursor.passed(_post("cc", later))


def test_an_old_single_id_cursor_file_still_loads(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"created_at": T, "id": "ff"}))
    cursor = Cursor(path)
    assert cursor.passed(_post("ff"))
    assert not cursor.passed(_post("aa"))  # the bug: a smaller id at the same time


def test_reset_forgets_everything(tmp_path):
    path = tmp_path / "cursor.json"
    Emitter(io.StringIO(), io.StringIO(), cursor=Cursor(path)).emit([_post("aa")])
    cursor = Cursor(path)
    cursor.reset()
    assert not cursor.passed(_post("aa"))
