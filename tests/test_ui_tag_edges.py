"""Tag edges for the Explorer graph: IDF-weighted, top-k per node, server-side.

The old client drew an edge for every pair sharing two tags, unweighted and
O(n^2). These pin the replacement's behaviour: a rare shared tag says more than
a common one, each node keeps only its strongest few, and generic hub tags and
date stamps do not wire the whole brain together.
"""

from __future__ import annotations

import math

from gingugu.ui_api.tag_edges import is_date_tag, tag_edges


def _pairs(edges) -> set[tuple[str, str]]:
    return {(a, b) for a, b, _ in edges}


def test_rare_shared_tag_outweighs_common_one():
    tags = {"a": ["rare", "common"], "b": ["rare"], "c": ["common"]}
    for i in range(20):
        tags[f"filler{i}"] = ["common"]
    edges = tag_edges(tags, top_k=1)
    weights = {(a, b): w for a, b, w in edges}
    # The rare tag carries the higher IDF. (a, c) may still appear: c's own
    # single pick is a, and the edge set is the union of every node's picks.
    assert weights[("a", "b")] > 10 * weights[("a", "c")]


def test_weight_is_sum_of_shared_idf():
    tags = {"a": ["x", "y"], "b": ["x", "y"], "c": ["x"], "d": ["z"]}
    n = len(tags)
    (edge,) = [e for e in tag_edges(tags, top_k=5) if e[:2] == ("a", "b")]
    expected = math.log(n / 3) + math.log(n / 2)
    assert edge[2] == round(expected, 3)


def test_pairs_are_ordered_unique_and_never_self():
    tags = {"b": ["t"], "a": ["t"], "c": ["t"]}
    edges = tag_edges(tags, top_k=5)
    assert len(edges) == len(_pairs(edges))
    for a, b, _ in edges:
        assert a < b


def test_top_k_bounds_each_nodes_own_picks():
    # A clique of 12 sharing one tag: every node could link to 11 others.
    tags = {f"n{i:02d}": ["shared"] for i in range(12)}
    edges = tag_edges(tags, top_k=3, max_df=100)
    assert len(edges) <= 3 * 12
    degree: dict[str, int] = {}
    for a, b, _ in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    assert all(d >= 3 for d in degree.values())


def test_hub_tags_above_max_df_make_no_edges():
    tags = {f"n{i}": ["mr-boomtastic"] for i in range(10)}
    assert tag_edges(tags, top_k=5, max_df=5) == []


def test_singleton_tags_make_no_edges():
    assert tag_edges({"a": ["only-a"], "b": ["only-b"]}, top_k=5) == []


def test_date_tags_excluded_by_default_and_opt_in():
    tags = {"a": ["2026-10-20"], "b": ["2026-10-20"], "c": ["other"]}
    assert tag_edges(tags, top_k=5) == []
    assert _pairs(tag_edges(tags, top_k=5, include_dates=True)) == {("a", "b")}


def test_is_date_tag():
    assert is_date_tag("2026-10-20")
    assert is_date_tag("2026-10")
    assert not is_date_tag("v0.25.0")
    assert not is_date_tag("pr-124")
    assert not is_date_tag("43rd-sail")


def test_deterministic_and_sorted_by_weight():
    tags = {"a": ["x", "y"], "b": ["x", "y"], "c": ["x"], "d": ["y"], "e": ["q"]}
    first = tag_edges(tags, top_k=2)
    assert first == tag_edges(dict(reversed(list(tags.items()))), top_k=2)
    weights = [w for _, _, w in first]
    assert weights == sorted(weights, reverse=True)


def test_empty_input():
    assert tag_edges({}, top_k=5) == []
