"""Tests for the retrieval benchmark toolset (bench/)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.dataset import load_dataset
from bench.metrics import estimate_tokens, mean, mrr, precision_at_k, recall_at_k
from bench.runner import FIXTURE_WEIGHTS as WEIGHTS
from bench.runner import build_fixture_db, run_benchmark
from gingugu.embeddings import FastEmbedProvider

FIXTURE = Path(__file__).parent.parent / "bench" / "datasets" / "fixture.json"


# --- metrics ---------------------------------------------------------------


def test_recall_at_k():
    assert recall_at_k(["a", "b"], ["a", "x", "b"], 3) == 1.0
    assert recall_at_k(["a", "b"], ["a", "x", "b"], 2) == 0.5
    assert recall_at_k(["a"], ["x", "y"], 2) == 0.0
    assert recall_at_k([], ["x"], 1) == 0.0


def test_precision_at_k():
    assert precision_at_k(["a", "b"], ["a", "b", "x", "y"], 4) == 0.5
    assert precision_at_k(["a"], ["a"], 5) == pytest.approx(0.2)
    assert precision_at_k(["a"], [], 5) == 0.0
    assert precision_at_k(["a"], ["a"], 0) == 0.0


def test_mrr():
    assert mrr(["a"], ["a", "b"]) == 1.0
    assert mrr(["a"], ["x", "a"]) == 0.5
    assert mrr(["a"], ["x", "y"]) == 0.0
    assert mrr([], ["x"]) == 0.0


def test_estimate_tokens_and_mean():
    assert estimate_tokens(["abcd" * 10]) == 10
    assert estimate_tokens([]) == 0
    assert mean([1.0, 0.0]) == 0.5
    assert mean([]) == 0.0


# --- dataset ---------------------------------------------------------------


def test_fixture_dataset_loads_and_validates():
    ds = load_dataset(FIXTURE)
    assert ds.is_fixture
    assert len(ds.questions) >= 5
    assert len(ds.memories) >= 10


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"version": 2}, "unsupported version"),
        ({"questions": []}, "no questions"),
    ],
)
def test_dataset_validation_rejects(tmp_path, mutation, message):
    raw = json.loads(FIXTURE.read_text())
    raw.update(mutation)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=message):
        load_dataset(bad)


def test_dataset_rejects_unknown_relevant_key(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    raw["questions"][0]["relevant"] = ["no-such-key"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="unknown memory keys"):
        load_dataset(bad)


# --- runner ----------------------------------------------------------------


def test_fixture_run_end_to_end():
    ds = load_dataset(FIXTURE)
    conn, key_to_id = build_fixture_db(ds)
    try:
        report = run_benchmark(
            ds, conn, weights=WEIGHTS, decay_lambda=0.01, ks=(1, 5), key_to_id=key_to_id
        )
    finally:
        conn.close()

    assert report.mode == "fixture"
    assert report.retrieval == "bm25-only"
    assert len(report.results) == len(ds.questions)
    for name in ("mrr", "recall@1", "recall@5", "precision@1", "precision@5", "tokens"):
        assert name in report.aggregates
    # The corpus is small and the labels are exact: retrieval should not be
    # useless. This is a floor against total regression, not a quality bar.
    assert report.aggregates["recall@5"] > 0.5
    assert set(report.by_kind) == {"single", "multi"}
    # "multi" questions have two relevant memories each: recall@1 can never
    # reach 1.0 for them (only one slot), while recall@5 can. A benchmark
    # that only ever calls at max(ks) and slices would show these equal -
    # this is the assertion that proves separate calls per k are happening.
    assert report.by_kind["multi"]["recall@1"] < report.by_kind["multi"]["recall@5"]


def test_fixture_run_is_deterministic():
    ds = load_dataset(FIXTURE)

    def one_run() -> dict[str, float]:
        conn, key_to_id = build_fixture_db(ds)
        try:
            return run_benchmark(
                ds, conn, weights=WEIGHTS, decay_lambda=0.01, ks=(5,), key_to_id=key_to_id
            ).aggregates
        finally:
            conn.close()

    assert one_run() == one_run()


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)  # cold-cache model download; see the fastembed CI-hang lesson
def test_fixture_run_with_real_embeddings():
    """Hybrid pass over the fixture with the real fastembed provider.

    Excluded from the default matrix run (``-m "not bench_embeddings"``) and
    run once in a dedicated CI job instead - nine matrix cells re-downloading
    an 80MB model on every push is pure waste, and this suite has already
    hung once on an unguarded fastembed fetch (v0.12.0, PR #36). This test is
    the deliberate, explicitly-opted-in exception that incident's fix
    anticipated, not a reopening of it: the default ``offline_embeddings``
    autouse fixture in conftest.py is untouched for every other test.
    """
    ds = load_dataset(FIXTURE)
    embedder = FastEmbedProvider()
    conn, key_to_id = build_fixture_db(ds, embedder=embedder)
    try:
        report = run_benchmark(
            ds,
            conn,
            weights=WEIGHTS,
            decay_lambda=0.01,
            embedder=embedder,
            ks=(1, 5),
            key_to_id=key_to_id,
        )
    finally:
        conn.close()

    assert report.retrieval == "hybrid"
    assert report.aggregates["recall@5"] > 0.5
    assert report.by_kind["multi"]["recall@1"] < report.by_kind["multi"]["recall@5"]


def test_benchmark_does_not_mutate_access_counts():
    ds = load_dataset(FIXTURE)
    conn, key_to_id = build_fixture_db(ds)
    try:
        before = conn.execute("SELECT SUM(access_count) FROM memories").fetchone()[0]
        run_benchmark(ds, conn, weights=WEIGHTS, decay_lambda=0.01, ks=(5,), key_to_id=key_to_id)
        after = conn.execute("SELECT SUM(access_count) FROM memories").fetchone()[0]
    finally:
        conn.close()
    assert before == after == 0
