"""Tests for the retrieval benchmark toolset (bench/)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from bench.dataset import load_dataset
from bench.metrics import estimate_tokens, mean, mrr, precision_at_k, recall_at_k
from bench.runner import FIXTURE_WEIGHTS as WEIGHTS
from bench.runner import build_fixture_db, run_benchmark
from gingugu import search as search_mod
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


# --- graded ages -----------------------------------------------------------


@pytest.mark.parametrize("bad_age", [-1, True, 1.5, "30"])
def test_dataset_rejects_bad_age_days(tmp_path, bad_age):
    """A bad age is rejected, never coerced.

    ``True`` is in here on purpose: it is an ``int`` to ``isinstance`` and
    would sail through a bare type check as "1 day old", which is how a
    mislabeled cohort ends up scoring as though it were correctly ordered.
    """
    raw = json.loads(FIXTURE.read_text())
    raw["memories"][0]["age_days"] = bad_age
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="age_days"):
        load_dataset(bad)


def test_age_days_backdates_every_timestamp():
    """``age_days`` moves the row into the past, and moves all four stamps.

    ``last_accessed`` matters as much as ``created_at`` here: leaving it at
    "now" would mark a 120-day-old memory as just-read, which is a separate
    ranking signal and would confound any cohort measured through it.
    """
    ds = load_dataset(FIXTURE)
    by_key = {m.key: m for m in ds.memories}
    conn, key_to_id = build_fixture_db(ds)
    try:
        rows = {}
        for key in by_key:
            rows[key] = conn.execute(
                "SELECT created_at, updated_at, last_accessed, last_confirmed "
                "FROM memories WHERE id = ?",
                (key_to_id[key],),
            ).fetchone()
    finally:
        conn.close()

    aged = {k: v for k, v in by_key.items() if v.age_days}
    assert aged, "fixture no longer carries a graded-age cohort"

    for key, fm in aged.items():
        row = rows[key]
        delta = datetime.fromisoformat(rows_now(rows)) - datetime.fromisoformat(row["created_at"])
        assert delta.days == fm.age_days, f"{key}: aged {delta.days}d, declared {fm.age_days}d"
        # All four stamps move together, or the row is internally inconsistent.
        assert row["updated_at"] == row["created_at"] == row["last_accessed"]
        assert row["last_confirmed"] == row["created_at"]

    # A row that declared no age is left exactly where create() put it.
    unaged = [k for k, v in by_key.items() if not v.age_days]
    assert unaged, "fixture has no present-day rows left to compare against"


def rows_now(rows: dict) -> str:
    """The newest ``created_at`` in the build, i.e. the run's own "now".

    Derived from the data rather than from ``utcnow()`` so the assertion
    cannot fail on the handful of milliseconds between building the DB and
    reading it back.
    """
    return max(r["created_at"] for r in rows.values())


# --- template families (board item 1) --------------------------------------
#
# The cohort in the ``release-handoffs`` namespace is an instrument, not a
# feature: five memories sharing a title shape and an identical opening
# paragraph, differing by one sentence and by age. The two tests below are the
# before/after pair for board item 1, and they pull in opposite directions on
# purpose - any fix has to satisfy both.


def _handoff_family(ds) -> dict[str, str]:
    return {m.key: m.content for m in ds.memories if m.namespace == "release-handoffs"}


def _rank_in_family(conn, key_to_id, query: str) -> list[str]:
    ns_id = conn.execute("SELECT id FROM namespaces WHERE name = 'release-handoffs'").fetchone()[
        "id"
    ]
    hits = search_mod.search(
        conn, query=query, namespace_id=ns_id, limit=5, weights=WEIGHTS, decay_lambda=0.01
    )
    id_to_key = {v: k for k, v in key_to_id.items()}
    return [id_to_key[m.id] for m in hits]


def test_handoff_family_members_are_equal_length():
    """The family must stay length-balanced or it measures the wrong thing.

    An earlier draft of this cohort made the older siblings longer, on the
    theory that real stale handoffs accumulate prose. Measured, that put the
    newest member at 556 chars against the oldest at 1405, and BM25 length
    normalization then handed the newest rank 1 on every query - the probe
    "passed" while measuring nothing but document length. This test is what
    stops that confound being reintroduced by a well-meaning edit.
    """
    lengths = [len(c) for c in _handoff_family(load_dataset(FIXTURE)).values()]
    assert len(lengths) == 5
    assert (max(lengths) - min(lengths)) / max(lengths) < 0.10


def test_newest_family_member_wins_a_scaffolding_query():
    """Regression guard: 'where did we leave off' returns the NEWEST handoff.

    Passes today. With length equalized, freshness breaks a tie that nothing
    else breaks. It is recorded because it is the thing a family-collapse fix
    is most likely to cost, not because it is currently broken.
    """
    ds = load_dataset(FIXTURE)
    conn, key_to_id = build_fixture_db(ds)
    try:
        ranked = _rank_in_family(conn, key_to_id, "where did we leave off on checkout-api")
    finally:
        conn.close()
    assert ranked[0] == "handoff-v2-5"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BOARD ITEM 1, template/sibling noise. 'nightly ledger reconciliation job' "
        "appears verbatim in handoff-v2-1 and nowhere else in the corpus, yet it "
        "returns THIRD - behind the two newest siblings, neither of which contains "
        "a single one of those terms. Shared scaffolding plus freshness outweighs a "
        "unique exact match. strict=True on purpose: when the fix lands this XPASSes "
        "and fails the suite, which is the prompt to delete this marker."
    ),
)
def test_unique_phrase_outranks_its_template_siblings():
    ds = load_dataset(FIXTURE)
    conn, key_to_id = build_fixture_db(ds)
    try:
        ranked = _rank_in_family(
            conn, key_to_id, "what happened to the nightly ledger reconciliation job"
        )
    finally:
        conn.close()
    assert ranked[0] == "handoff-v2-1"
