"""Tests for the generated template-family probe set (bench/probes.py).

The load-bearing test here is ``test_every_generated_label_is_provably_correct``.
This module's whole claim is that its labels are objectively verifiable rather
than judged - that is what justifies trusting a set nobody hand-checked. If
that property is not enforced, the generator is just producing a large number
of confident guesses, which is worse than 30 honest ones.
"""

from __future__ import annotations

import sqlite3

import pytest

from bench.dataset import load_dataset
from bench.probes import (
    MIN_FAMILY,
    QUESTIONS_PER_FAMILY,
    content_ngrams,
    find_families,
    generate,
    signature,
    unique_phrase,
    write,
)
from gingugu.database import migrate
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

# Shared scaffolding, plus one distinguishing sentence per member - the shape
# the generator exists to find.
_BOILER = (
    "Written at the end of the release with state and open items for whoever "
    "picks this up next. Standing rules are unchanged and nothing here needs "
    "action unless an open item says so."
)


def _brain(members: list[tuple[str, str]], namespace: str = "proj") -> sqlite3.Connection:
    from pathlib import Path

    from gingugu.config import Config
    from gingugu.embeddings import NullEmbeddingProvider

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    cfg = Config(
        db_path=Path(":memory:"),
        namespace=None,
        namespace_path=None,
        auto_context_limit=10,
        decay_lambda=0.01,
    )
    ns = NamespaceManager(conn, cfg).get_or_create(namespace)
    store = MemoryStore(conn, embedder=NullEmbeddingProvider())
    for title, payload in members:
        store.create(
            namespace_id=ns.id,
            type=MemoryType.WORKFLOW,
            title=title,
            content=f"{_BOILER} {payload}",
            confidence=Confidence.VERIFIED,
        )
    return conn


_SHARDS = ("alpha", "beta", "gamma", "delta", "epsilon")


def _family(n: int) -> list[tuple[str, str]]:
    return [
        (
            f"Release handoff: checkout-api v2.{i} - state, open items, next steps",
            f"Open item: the telemetry exporter for shard {_SHARDS[i - 1]} "
            "remains unconfigured after the cutover.",
        )
        for i in range(1, n + 1)
    ]


# --- signature -------------------------------------------------------------


def test_signature_collapses_siblings_but_not_unrelated_titles():
    a = signature("BOARD OF RECORD 2026-08-21 (26th sail): 6 items - a bug is back")
    b = signature("BOARD OF RECORD 2026-09-07 (25th sail): 7 items - no-code sail")
    assert a == b == "board of record"
    assert signature("Release handoff: checkout-api v2.1 - state") != a


def test_content_ngrams_skips_punctuation_and_stopword_only_grams():
    # A gram carrying punctuation cannot be trusted to survive both FTS5
    # tokenization and a literal substring check.
    assert not any("`code`" in g for g in content_ngrams("a `code` token here now", 3))
    # Needs two content words; "of the and to be" is not an identifier.
    assert content_ngrams("of the and to be", 4) == []


# --- families --------------------------------------------------------------


def test_find_families_requires_the_minimum_size():
    conn = _brain(_family(MIN_FAMILY - 1))
    rows = conn.execute(
        "SELECT m.id, m.title, m.content, n.name AS ns FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id"
    ).fetchall()
    assert find_families(rows) == {}
    conn.close()

    conn = _brain(_family(MIN_FAMILY))
    rows = conn.execute(
        "SELECT m.id, m.title, m.content, n.name AS ns FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id"
    ).fetchall()
    assert len(find_families(rows)) == 1
    conn.close()


def test_unique_phrase_returns_none_when_members_are_identical():
    """Identical siblings have nothing that distinguishes them, and the honest
    answer is no question rather than an arbitrary one."""
    conn = _brain([(f"Release handoff: checkout-api v2.{i} - state", "") for i in (1, 2, 3)])
    rows = conn.execute(
        "SELECT m.id, m.title, m.content, n.name AS ns FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id"
    ).fetchall()
    assert unique_phrase(rows[0], list(rows)) is None
    conn.close()


# --- generation ------------------------------------------------------------


def test_every_generated_label_is_provably_correct():
    """THE load-bearing guarantee: each labelled answer is the ONLY memory in
    its namespace containing the queried phrase, checked against the corpus.

    A generated set is only worth more than a hand-labelled one if this holds.
    """
    conn = _brain(_family(5))
    dataset = generate(conn)
    assert dataset["questions"]

    for q in dataset["questions"]:
        phrase = q["query"].removeprefix("what did we say about ").lower()
        holders = [
            row["id"]
            for row in conn.execute("SELECT id, content FROM memories")
            if phrase in row["content"].lower()
        ]
        assert holders == q["relevant"], f"{q['id']}: phrase in {len(holders)} memories, not 1"
    conn.close()


def test_generation_caps_questions_per_family():
    """One 55-member family must not be allowed to dominate the metric."""
    conn = _brain(_family(5))
    dataset = generate(conn)
    assert len(dataset["questions"]) == QUESTIONS_PER_FAMILY
    conn.close()


def test_generated_dataset_loads_through_the_bench_schema(tmp_path):
    """It has to be a real dataset, not merely a plausible dict."""
    conn = _brain(_family(4))
    out = tmp_path / "probes.json"
    write(generate(conn), out)
    conn.close()

    ds = load_dataset(out)
    assert not ds.is_fixture  # real-brain shape: UUIDs, no inline memories
    assert ds.questions
    assert all(q.kind == "single" and len(q.relevant) == 1 for q in ds.questions)


def test_generation_is_deterministic():
    conn = _brain(_family(5))
    first, second = generate(conn), generate(conn)
    conn.close()
    assert first == second


@pytest.mark.parametrize("size", [0, 1, 2])
def test_no_questions_without_a_family(size):
    conn = _brain(_family(size))
    assert generate(conn)["questions"] == []
    conn.close()
