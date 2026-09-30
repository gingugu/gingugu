"""Tripwires: memories that stop a tool call at the moment it would repeat a mistake.

The matching is plain regex over the pending call, never similarity. A risky
command and a harmless one can read alike to an encoder; a pattern the user
wrote cannot be talked out of what it says.
"""

from __future__ import annotations

import pytest

from gingugu import tripwire
from gingugu.migrations import LATEST_SCHEMA_VERSION
from gingugu.models import Confidence, MemoryType


@pytest.fixture
def ns_id(namespaces):
    return namespaces.get_or_create("test-ns").id


def _mem(store, ns_id, title="Never delete a stack parent", **kw):
    return store.create(
        namespace_id=ns_id,
        type=kw.pop("type", MemoryType.PREFERENCE),
        title=title,
        content=kw.pop("content", "Retarget the children to main before deleting the parent."),
        **kw,
    )


# --- migration ---------------------------------------------------------------


def test_migration_creates_the_tripwires_table(db):
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(tripwires)")}
    assert cols == {"id", "memory_id", "tool_pattern", "input_pattern", "created_at"}
    assert LATEST_SCHEMA_VERSION >= 16


def test_hard_deleting_a_memory_takes_its_tripwires_with_it(db, store, ns_id):
    mem = _mem(store, ns_id)
    tripwire.add_tripwire(db.conn, mem.id, "Bash", r"gh pr merge")
    db.conn.commit()
    assert store.delete(mem.id)
    assert db.conn.execute("SELECT COUNT(*) FROM tripwires").fetchone()[0] == 0


# --- match_text --------------------------------------------------------------


def test_match_text_for_bash_is_the_command():
    assert tripwire.match_text("Bash", {"command": "git push", "timeout": 5}) == "git push"


@pytest.mark.parametrize("tool", ["Edit", "Write", "Read", "NotebookEdit"])
def test_match_text_for_file_tools_is_the_path(tool):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    assert tripwire.match_text(tool, {key: "/r/migrations/x.py", "content": "..."}) == (
        "/r/migrations/x.py"
    )


def test_match_text_for_anything_else_is_stable_json():
    a = tripwire.match_text("mcp__x__y", {"b": 1, "a": "z"})
    b = tripwire.match_text("mcp__x__y", {"a": "z", "b": 1})
    assert a == b and '"a": "z"' in a


def test_match_text_survives_a_non_dict_input():
    assert tripwire.match_text("Bash", None) == ""


# --- validate ----------------------------------------------------------------


def test_validate_accepts_good_patterns():
    assert tripwire.validate_patterns("Bash", r"gh pr merge.*--delete-branch") is None


@pytest.mark.parametrize(
    "tool, pattern",
    [("Bash", "("), ("(", "x"), ("", "x"), ("Bash", ""), ("Bash", "x" * 501)],
)
def test_validate_rejects_bad_patterns(tool, pattern):
    assert isinstance(tripwire.validate_patterns(tool, pattern), str)


# --- matching ----------------------------------------------------------------


def _wire(memory_id="m1", tool="Bash", pattern=r"gh pr merge", title="t"):
    return tripwire.Tripwire(
        id=f"w-{memory_id}-{pattern}",
        memory_id=memory_id,
        tool_pattern=tool,
        input_pattern=pattern,
        title=title,
        summary="s",
        namespace="crow",
        type="preference",
    )


def test_tool_pattern_is_a_full_match_not_a_prefix():
    wires = [_wire(tool="Bash", pattern="sleep")]
    assert tripwire.matching(wires, "Bash", {"command": "sleep 1"})
    assert not tripwire.matching(wires, "BashOutput", {"command": "sleep 1"})


def test_tool_pattern_alternation_works():
    wires = [_wire(tool="Edit|Write", pattern=r"migrations/")]
    assert tripwire.matching(wires, "Write", {"file_path": "src/migrations/a.py"})
    assert not tripwire.matching(wires, "Read", {"file_path": "src/migrations/a.py"})


def test_input_pattern_is_a_search_anywhere():
    wires = [_wire(pattern=r"--delete-branch")]
    hits = tripwire.matching(wires, "Bash", {"command": "gh pr merge 90 --squash --delete-branch"})
    assert [h.memory_id for h in hits] == ["m1"]


def test_a_memory_with_two_matching_wires_trips_once():
    wires = [_wire(pattern="gh"), _wire(pattern="merge")]
    assert len(tripwire.matching(wires, "Bash", {"command": "gh pr merge"})) == 1


def test_matching_caps_how_many_memories_trip():
    wires = [_wire(memory_id=f"m{i}") for i in range(10)]
    hits = tripwire.matching(wires, "Bash", {"command": "gh pr merge"})
    assert len(hits) == tripwire.MAX_TRIPPED


def test_a_corrupt_stored_pattern_is_skipped_not_raised():
    wires = [_wire(pattern="("), _wire(memory_id="m2", pattern="merge")]
    assert [h.memory_id for h in tripwire.matching(wires, "Bash", {"command": "merge"})] == ["m2"]


# --- CRUD + load -------------------------------------------------------------


def test_add_list_remove_round_trip(db, store, ns_id):
    mem = _mem(store, ns_id)
    row = tripwire.add_tripwire(db.conn, mem.id, "Bash", r"gh pr merge")
    assert row["memory_id"] == mem.id and row["id"]
    listed = tripwire.list_tripwires(db.conn, memory_id=mem.id)
    assert [w["id"] for w in listed] == [row["id"]]
    assert listed[0]["title"] == mem.title and listed[0]["namespace"] == "test-ns"
    assert tripwire.remove_tripwire(db.conn, row["id"]) is True
    assert tripwire.remove_tripwire(db.conn, row["id"]) is False
    assert tripwire.list_tripwires(db.conn, memory_id=mem.id) == []


def test_list_filters_by_namespace(db, store, namespaces):
    a = _mem(store, namespaces.get_or_create("alpha").id)
    b = _mem(store, namespaces.get_or_create("beta").id)
    tripwire.add_tripwire(db.conn, a.id, "Bash", "x")
    tripwire.add_tripwire(db.conn, b.id, "Bash", "y")
    listed = tripwire.list_tripwires(db.conn, namespaces=["beta"])
    assert [w["memory_id"] for w in listed] == [b.id]


def test_add_rejects_a_missing_memory(db):
    with pytest.raises(ValueError, match="not found"):
        tripwire.add_tripwire(db.conn, "nope", "Bash", "x")


def test_add_rejects_an_invalid_pattern(db, store, ns_id):
    mem = _mem(store, ns_id)
    with pytest.raises(ValueError):
        tripwire.add_tripwire(db.conn, mem.id, "Bash", "(")


def test_load_scopes_to_namespaces_and_skips_retired_memories(db, store, namespaces):
    live = _mem(store, namespaces.get_or_create("crow").id, title="live")
    other = _mem(store, namespaces.get_or_create("elsewhere").id, title="other ns")
    retired = _mem(store, namespaces.get_or_create("crow").id, title="retired")
    old = _mem(store, namespaces.get_or_create("crow").id, title="superseded")
    for m in (live, other, retired, old):
        tripwire.add_tripwire(db.conn, m.id, "Bash", "x")
    store.update(retired.id, confidence=Confidence.DEPRECATED)
    db.conn.execute(
        "INSERT INTO relations(id, source_id, target_id, relation_type, created_at) "
        "VALUES ('r1', ?, ?, 'supersedes', '2026-01-01T00:00:00+00:00')",
        (live.id, old.id),
    )
    db.conn.commit()
    loaded = tripwire.load_tripwires(db.conn, ["crow", "gingugu"])
    assert [w.title for w in loaded] == ["live"]
    assert loaded[0].namespace == "crow" and loaded[0].summary


def test_pinned_memories_still_trip(db, store, ns_id):
    """Unlike prompt recall, a pin being loaded at session start is not the same
    as it being in front of the agent at the moment of the action."""
    mem = _mem(store, ns_id)
    db.conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (mem.id,))
    tripwire.add_tripwire(db.conn, mem.id, "Bash", "x")
    db.conn.commit()
    assert [w.memory_id for w in tripwire.load_tripwires(db.conn, ["test-ns"])] == [mem.id]


# --- render ------------------------------------------------------------------


def test_render_reason_names_the_memory_and_the_way_through():
    reason = tripwire.render_reason([_wire(title="Never delete a stack parent")])
    assert "Never delete a stack parent" in reason
    assert "id=m1" in reason
    assert "re-issue" in reason.lower()
