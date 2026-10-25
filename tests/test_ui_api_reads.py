"""The Explorer API's read surfaces through ``ui_api.dispatch``: info, graph,
memory detail, stats.

``dispatch(path, params, env)`` is the one entry point both the local
``gingugu ui`` process and ``gingugu serve``'s ``/ui/api`` routes call, so
these pin the payloads once for both modes. Every read goes over a read-only
connection and must never move an access signal.
"""

from __future__ import annotations

import json
import sqlite3

from gingugu import __version__
from gingugu.migrations import LATEST_SCHEMA_VERSION
from gingugu.ui_api import UiEnv, dispatch
from tests.test_embeddings import FakeEmbedder
from tests.ui_api_fixtures import BODY_MARKER, T2, snapshot_signals

NODE_KEYS = {
    "id", "ns", "type", "conf", "title", "pinned", "access",
    "created", "updated", "accessed", "confirmed", "tags",
}  # fmt: skip


def _env(seeded) -> UiEnv:
    return UiEnv(db_path=seeded.db_path, embedder=FakeEmbedder)


def _get(seeded, path, params=None):
    return dispatch(path, params or {}, _env(seeded))


# --- info ----------------------------------------------------------------------


def test_info_reports_version_schema_and_counts(seeded):
    status, body = _get(seeded, "/info")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == __version__
    assert body["schema_version"] == LATEST_SCHEMA_VERSION
    assert body["memories"] == 5
    assert body["relations"] == 2


def test_missing_local_brain_is_503_not_a_crash(tmp_path):
    env = UiEnv(db_path=tmp_path / "nope.db", embedder=FakeEmbedder)
    status, body = dispatch("/graph", {}, env)
    assert status == 503
    assert body["ok"] is False
    assert not (tmp_path / "nope.db").exists()  # a read never creates a brain


def test_unknown_route_is_404(seeded):
    status, body = _get(seeded, "/export")
    assert status == 404
    assert body["ok"] is False


# --- graph ---------------------------------------------------------------------


def test_graph_nodes_are_light_and_never_carry_bodies(seeded):
    status, body = _get(seeded, "/graph")
    assert status == 200 and body["ok"] is True
    assert len(body["nodes"]) == 5
    for node in body["nodes"]:
        assert set(node) == NODE_KEYS
    assert BODY_MARKER not in json.dumps(body)


def test_graph_tags_are_indexes_into_the_tag_table(seeded):
    _, body = _get(seeded, "/graph")
    by_id = {n["id"]: n for n in body["nodes"]}
    rule = by_id[seeded.ids["rule"]]
    assert sorted(body["tags"][i] for i in rule["tags"]) == ["git", "rare-x", "stacking"]


def test_graph_namespaces_carry_counts(seeded):
    _, body = _get(seeded, "/graph")
    counts = {ns["name"]: ns["count"] for ns in body["namespaces"]}
    assert counts == {"crow": 2, "gingugu": 3}
    ids = {ns["name"]: ns["id"] for ns in body["namespaces"]}
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id[seeded.ids["rule"]]["ns"] == ids["crow"]


def test_graph_flags_pins_and_keeps_deprecated(seeded):
    _, body = _get(seeded, "/graph")
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id[seeded.ids["rule"]]["pinned"] is True
    assert by_id[seeded.ids["lesson"]]["pinned"] is False
    # Never-forget: deprecated memories still render.
    assert by_id[seeded.ids["old"]]["conf"] == "deprecated"


def test_graph_relations_and_tag_edges(seeded):
    _, body = _get(seeded, "/graph")
    rels = {(r["s"], r["t"], r["type"]) for r in body["relations"]}
    assert (seeded.ids["lesson"], seeded.ids["rule"], "caused_by") in rels
    assert (seeded.ids["schema"], seeded.ids["old"], "supersedes") in rels
    for r in body["relations"]:
        assert set(r) == {"s", "t", "type", "created"}
    pairs = {frozenset((e["s"], e["t"])) for e in body["tag_edges"]}
    assert frozenset((seeded.ids["rule"], seeded.ids["lesson"])) in pairs
    for e in body["tag_edges"]:
        assert set(e) == {"s", "t", "w"} and e["w"] > 0


def test_graph_version_changes_when_the_brain_changes(seeded):
    _, before = _get(seeded, "/graph")
    _, again = _get(seeded, "/graph")
    assert before["version"] == again["version"]
    conn = sqlite3.connect(seeded.db_path)
    conn.execute(
        "UPDATE memories SET updated_at = '2030-01-01T00:00:00+00:00' WHERE id = ?",
        (seeded.ids["rule"],),
    )
    conn.commit()
    conn.close()
    _, after = _get(seeded, "/graph")
    assert after["version"] != before["version"]


# --- memory detail -------------------------------------------------------------


def test_memory_detail_carries_body_tags_and_namespace(seeded):
    status, body = _get(seeded, f"/memory/{seeded.ids['rule']}")
    assert status == 200 and body["ok"] is True
    mem = body["memory"]
    assert BODY_MARKER in mem["content"]
    assert mem["namespace"] == "crow"
    assert mem["pinned"] is True
    assert sorted(mem["tags"]) == ["git", "rare-x", "stacking"]


def test_memory_detail_relations_both_directions_with_titles(seeded):
    _, body = _get(seeded, f"/memory/{seeded.ids['rule']}")
    assert body["relations"]["out"] == []
    (incoming,) = body["relations"]["in"]
    assert incoming["id"] == seeded.ids["lesson"]
    assert incoming["type"] == "caused_by"
    assert incoming["title"] == "Retarget child PRs before merging"


def test_memory_detail_tripwires_claims_and_accesses(seeded):
    _, rule = _get(seeded, f"/memory/{seeded.ids['rule']}")
    (wire,) = rule["tripwires"]
    assert wire["tool_pattern"] == "Bash"
    assert rule["accesses"] == [T2]
    _, schema = _get(seeded, f"/memory/{seeded.ids['schema']}")
    (claim,) = schema["claims"]
    assert (claim["kind"], claim["ref"], claim["state"]) == ("pr", "gingugu#124", "open")


def test_unknown_memory_is_404(seeded):
    status, body = _get(seeded, "/memory/not-a-real-id")
    assert status == 404 and body["ok"] is False


# --- stats ---------------------------------------------------------------------


def test_stats_whole_brain_and_one_namespace(seeded):
    status, whole = _get(seeded, "/stats")
    assert status == 200 and whole["stats"]["total_memories"] == 5
    _, crow = _get(seeded, "/stats", {"ns": "crow"})
    assert crow["stats"]["total_memories"] == 2


def test_stats_unknown_namespace_is_400(seeded):
    status, body = _get(seeded, "/stats", {"ns": "no-such-ns"})
    assert status == 400 and body["ok"] is False


# --- the observer rule ---------------------------------------------------------


def test_reads_never_move_access_signals(seeded):
    before = snapshot_signals(seeded.db_path)
    for path in ("/info", "/graph", "/stats", f"/memory/{seeded.ids['rule']}"):
        assert _get(seeded, path)[0] == 200
    assert snapshot_signals(seeded.db_path) == before


def test_memory_ids_must_be_plain(seeded):
    for bad in ("/memory/../graph", "/memory/a/b", "/memory/x%2Fy", "/memory/" + "a" * 200):
        assert _get(seeded, bad)[0] == 404
