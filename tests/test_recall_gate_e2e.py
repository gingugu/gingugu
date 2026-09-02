"""Involuntary recall, end to end, against a real encoder and real SQL.

``test_recall_gate.py`` hands ``select()`` candidates whose similarities were
chosen by hand. That pins the arithmetic, and it is the right shape for the
main matrix - offline, instant, deterministic. But it would keep passing if
``recall_sweep`` were completely broken: wrong SQL, an inverted cosine, or the
pinned and superseded exclusions silently not applying. Nothing in that file
touches a database or a model.

This closes that gap with the smallest thing that can: a real fastembed
encoder over a synthetic five-memory corpus, through the real schema, the real
FTS5 triggers, and the real query. It answers the only question the unit tests
cannot - does the pipeline actually retrieve the right thing, and actually
refuse the things it claims to refuse.

Marked ``bench_embeddings`` like the fixture benchmark, so it runs once in its
own CI job with a cached model rather than in all nine matrix cells.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from gingugu.database import Database
from gingugu.embeddings import FastEmbedProvider, pack
from gingugu.recall_gate import GateConfig, select, strip_affect
from gingugu.recall_sweep import connect_readonly, lexical_matches, sweep

NAMESPACE = "test-brain"

# Deliberately written so the distractors share the user's REGISTER without
# sharing his subject - the failure mode that drove the whole design.
CORPUS = [
    {
        "key": "target",
        "type": "bug",
        "title": "SQLite WAL checkpoint starves under a long-running read transaction",
        "content": (
            "A reader holding an open transaction pins the WAL and checkpointing "
            "cannot truncate it, so the wal file grows without bound until the "
            "reader commits. Close read transactions promptly."
        ),
    },
    {
        "key": "pinned",
        "type": "preference",
        "title": "SQLite WAL mode is always on, checkpoint tuning is never ad hoc",
        "content": (
            "Standing rule about the WAL checkpoint policy. This memory is PINNED, "
            "so it already loads at every session start and must never be injected."
        ),
        "pinned": True,
    },
    {
        "key": "superseded",
        "type": "decision",
        "title": "SQLite WAL checkpoint runs on every write (REPLACED)",
        "content": (
            "The old checkpoint policy for the wal file. A later decision replaced "
            "this one, so it must never be injected as though it were current."
        ),
    },
    {
        "key": "replacement",
        "type": "decision",
        "title": "Checkpoint policy moved to a size threshold",
        "content": "Supersedes the write-through checkpoint decision.",
    },
    {
        "key": "chatty",
        "type": "pattern",
        "title": "Reflection: the quiet session after the near-disaster, and what I took from it",
        "content": (
            "Nice work matey. A long first-person retrospective about how the day "
            "went, written in exactly the voice the user types in, about nothing "
            "technical in particular."
        ),
    },
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def brain(tmp_path, request):
    """A real on-disk brain with real vectors. Returns (path, {key: id})."""
    embedder = request.getfixturevalue("embedder")
    path = tmp_path / "memories.db"
    database = Database(path)
    database.connect()
    conn = database.conn

    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces (id, name, created_at, updated_at) VALUES (?,?,?,?)",
        (ns_id, NAMESPACE, _now(), _now()),
    )

    ids: dict[str, str] = {}
    for entry in CORPUS:
        mem_id = str(uuid.uuid4())
        ids[entry["key"]] = mem_id
        conn.execute(
            "INSERT INTO memories (id, namespace_id, type, title, content, confidence,"
            " created_at, updated_at, last_accessed, pinned)"
            " VALUES (?,?,?,?,?,'verified',?,?,?,?)",
            (
                mem_id,
                ns_id,
                entry["type"],
                entry["title"],
                entry["content"],
                _now(),
                _now(),
                _now(),
                1 if entry.get("pinned") else 0,
            ),
        )
        vec = embedder.encode(f"{entry['title']}\n\n{entry['content']}")
        conn.execute(
            "INSERT INTO memory_embeddings"
            " (memory_id, model, dim, embedding, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (mem_id, embedder.model_name, embedder.dim, pack(vec), _now(), _now()),
        )

    conn.execute(
        "INSERT INTO relations (id, source_id, target_id, relation_type, created_at)"
        " VALUES (?,?,?,'supersedes',?)",
        (str(uuid.uuid4()), ids["replacement"], ids["superseded"], _now()),
    )
    conn.commit()
    database.close()
    return path, ids


@pytest.fixture(scope="module")
def embedder():
    """Module-scoped: the ONNX model loads once for the file, not once per test.

    Six loads of a cached model is not slow enough to matter on its own, but
    this job exists because model work is expensive, so paying for it six times
    in the job created to pay for it once reads as an oversight.
    """
    return FastEmbedProvider()


def _run(path, embedder, prompt: str, *, config: GateConfig | None = None):
    cleaned = strip_affect(prompt)
    vec = embedder.encode(cleaned)
    conn = connect_readonly(path)
    try:
        cands = sweep(conn, list(vec), [NAMESPACE], config=config)
        lex = lexical_matches(conn, cleaned, [NAMESPACE])
    finally:
        conn.close()
    return select(cands, lexical_ids=lex, config=config)


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)  # cold-cache model download
def test_an_on_topic_prompt_surfaces_the_on_topic_memory(brain, embedder):
    path, ids = brain
    picked = _run(
        path,
        embedder,
        "why does the wal file keep growing while a long read transaction is open",
    )
    assert [c.id for c in picked] == [ids["target"]]


def _swept_ids(path, embedder, prompt: str) -> set[str]:
    """Every id the sweep is willing to CONSIDER, before any gate runs.

    The exclusions are a promise about the candidate set, so assert there. An
    assertion on the gated output would pass whenever the thresholds happened
    to reject the row for an unrelated reason, which is exactly how a test for
    a filter ends up proving nothing about the filter.
    """
    conn = connect_readonly(path)
    try:
        vec = embedder.encode(strip_affect(prompt))
        loose = GateConfig(bar=0.0, margin=0.0, require_lexical=False)
        return {c.id for c in sweep(conn, list(vec), [NAMESPACE], config=loose)}
    finally:
        conn.close()


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)
def test_a_pinned_memory_is_never_even_a_candidate(brain, embedder):
    """It already loads unconditionally at session start; injecting it pays twice."""
    path, ids = brain
    swept = _swept_ids(path, embedder, "what is our standing rule on the sqlite wal checkpoint")
    assert ids["pinned"] not in swept
    assert ids["target"] in swept  # the exclusion is targeted, not a broken query


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)
def test_a_superseded_memory_is_never_even_a_candidate(brain, embedder):
    """Replaced knowledge arriving as current is worse than no memory at all."""
    path, ids = brain
    swept = _swept_ids(path, embedder, "when does the sqlite wal checkpoint actually run")
    assert ids["superseded"] not in swept
    assert ids["replacement"] in swept  # the memory that replaced it still qualifies


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)
def test_a_conversational_prompt_surfaces_nothing(brain, embedder):
    """The register-matching failure the lexical half exists to prevent."""
    path, _ = brain
    assert _run(path, embedder, "nice work matey, lets commit and move on to the next one") == []


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)
def test_dropping_the_lexical_requirement_is_what_lets_the_reflection_through(brain, embedder):
    """Proves the lexical half is load-bearing rather than decorative.

    The same prompt that surfaces nothing above reaches the reflection once the
    keyword requirement is removed and only cosine is left to judge it.
    """
    path, ids = brain
    prompt = "nice work matey, lets commit and move on to the next one"
    loose = GateConfig(bar=0.0, margin=0.0, require_lexical=False)
    ranked = _run(path, embedder, prompt, config=loose)
    assert ranked[0].id == ids["chatty"]


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)
def test_the_sweep_opens_the_database_read_only(brain, embedder):
    path, _ = brain
    conn = connect_readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM memories")
    finally:
        conn.close()
