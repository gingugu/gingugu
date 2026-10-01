"""The grant model, and each store chokepoint on its own.

Several chokepoints are defence in depth: a hidden id the neighbour query
let through would still be dropped by ``store.get`` before reaching a
response, so the end-to-end canary cannot tell whether the inner layer
holds. Each one is pinned here directly, under a bound grant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gingugu import grants
from gingugu.capability import lane
from gingugu.database import Database
from gingugu.grants import FULL, READ, WRITE, Grant
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore
from tests.scoped_fixtures import GRANT
from tests.test_embeddings import FakeEmbedder

# --- the grant itself ---------------------------------------------------------


def test_levels_combine_explicit_and_wildcard():
    g = Grant("g", {"*": READ, "scratch": WRITE})
    assert g.level("scratch") == WRITE
    assert g.level("anything") == READ
    assert Grant("g", {"*": WRITE, "x": READ}).level("x") == WRITE
    assert Grant("g", {"x": READ}).level("y") is None


def test_only_wildcard_write_is_full():
    assert FULL.is_full
    assert not Grant("g", {"*": READ}).is_full
    assert not Grant("g", {"a": WRITE}).is_full


@pytest.mark.parametrize("bad", [{"a": "admin"}, {"": READ}])
def test_invalid_grants_are_rejected(bad):
    with pytest.raises(ValueError):
        Grant("g", bad)


def test_unbound_and_full_bind_are_unfenced(tmp_path):
    conn = Database(tmp_path / "g.db").connect()
    assert grants.current() is None
    with grants.bind(FULL, conn):
        assert grants.current() is None
        assert grants.scope_clause("x") == (None, [])
    with grants.bind(GRANT, conn):
        assert grants.current() is GRANT
    assert grants.current() is None


def test_a_grant_that_reads_nothing_scopes_to_nothing(tmp_path):
    conn = Database(tmp_path / "g.db").connect()
    with grants.bind(Grant("g", {"nowhere": READ}), conn):
        assert grants.scope_clause("m.namespace_id") == ("0", [])


# --- chokepoints, one at a time -----------------------------------------------


@pytest.fixture
def conn(brain):
    return Database(Path(brain.db_path)).connect()


def test_neighbour_traversal_never_crosses_the_fence(brain, conn):
    rel = RelationManager(conn)
    assert brain.ids["B1"] in rel.dampened_neighbour_ids([brain.ids["A1"]])
    with grants.bind(GRANT, conn):
        assert rel.dampened_neighbour_ids([brain.ids["A1"]]) == [brain.ids["A2"]]
        assert rel.dampened_neighbour_ids([brain.ids["G1"]]) == []


def test_get_relations_hides_the_far_end(brain, conn):
    rel = RelationManager(conn)
    with grants.bind(GRANT, conn):
        assert rel.related_ids(brain.ids["A1"]) == [brain.ids["A2"]]


def test_hidden_memory_does_not_exist_for_relate(brain, conn):
    rel = RelationManager(conn)
    with grants.bind(GRANT, conn):
        assert rel._exists(brain.ids["A1"])
        assert not rel._exists(brain.ids["B1"])


def test_touch_and_access_skip_hidden_rows(brain, conn):
    store = MemoryStore(conn)
    with grants.bind(GRANT, conn):
        assert store.touch_many([brain.ids["B1"], brain.ids["B2"]]) == 0
        assert store.record_accesses([brain.ids["B1"]]) == 0
        assert store.touch_many([brain.ids["A1"]]) == 1


def test_capability_lane_only_offers_readable_pointers(brain, conn):
    emb = FakeEmbedder()
    unfenced = lane(conn, emb, "alpha fetcher", namespace_id=None, exclude=set(), bar=0.0)
    assert brain.ids["B2"] in [mid for mid, _ in unfenced]
    with grants.bind(GRANT, conn):
        assert lane(conn, emb, "alpha fetcher", namespace_id=None, exclude=set(), bar=0.0) == []


def test_capability_lane_empty_scope_is_empty_not_everywhere(brain, conn):
    emb = FakeEmbedder()
    assert lane(conn, emb, "alpha fetcher", namespace_id=[], exclude=set(), bar=0.0) == []


def test_edge_degrees_count_only_visible_edges(brain, conn):
    rel = RelationManager(conn)
    a1 = brain.ids["A1"]
    row = rel.list_edges(memory_id=a1)["edges"]
    assert {e["source_degree"] for e in row if e["source_id"] == a1} == {2}  # A2 + hidden B1
    with grants.bind(GRANT, conn):
        fenced = rel.list_edges(memory_id=a1)["edges"]
    assert len(fenced) == 1
    assert fenced[0]["source_degree"] == 1


def test_get_relations_of_a_hidden_memory_is_empty(brain, conn):
    rel = RelationManager(conn)
    with grants.bind(GRANT, conn):
        assert rel.get_relations(brain.ids["B1"]) == []
