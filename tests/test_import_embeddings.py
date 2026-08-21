"""Imported memories must be semantically searchable, not keyword-only.

`memories` and `memory_embeddings` are kept in step by code, never by a
trigger: the FTS5 index has triggers and heals itself, embeddings do not, so a
vector exists only where some path deliberately wrote one. `memory_import`
wrote memory rows without ever writing a vector, so everything restored from a
backup landed permanently invisible to the semantic half of hybrid retrieval.

The startup backfill was not a repair path for this. It drains ONE batch of 32
per process, so a 1,423-memory restore needed 45 server restarts to finish, and
the "later writes will cover the rest" reasoning never applied to imported rows
- nothing writes them again.
"""

from __future__ import annotations

from pathlib import Path

from gingugu import embedding_sync, portability
from gingugu.config import Config
from gingugu.database import Database
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


class StubEmbedder:
    """Deterministic and offline. The suite never loads a real model."""

    dim = 3
    model_name = "stub"
    enabled = True

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, text: str) -> list[float]:
        self.calls += 1
        return [1.0, 0.0, 0.0] if "wal" in text.lower() else [0.0, 1.0, 0.0]

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


class BrokenEmbedder(StubEmbedder):
    """Encodes nothing and raises. The import must survive it."""

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model unavailable")


def _fresh_db() -> Database:
    cfg = Config(
        db_path=Path(":memory:"),
        namespace="test-ns",
        namespace_path=None,
        auto_context_limit=10,
        decay_lambda=0.05,
    )
    db = Database(cfg.db_path)
    db.connect()
    return db


def _count_embeddings(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]


def _seed(db: Database, config: Config, n: int = 3) -> dict:
    """Build a source store with embeddings and return its export payload."""
    store = MemoryStore(db.conn, embedder=StubEmbedder())
    ns = NamespaceManager(db.conn, config).get_or_create("proj")
    for i in range(n):
        store.create(
            namespace_id=ns.id,
            type=MemoryType.FACT,
            title=f"wal checkpoint note {i}",
            content="sqlite cannot truncate the log while a reader holds a snapshot",
        )
    return portability.export_data(db.conn)


def test_import_embeds_what_it_writes(db: Database, config: Config) -> None:
    """The headline regression: a restored memory is embedded on arrival."""
    payload = _seed(db, config)

    dest = _fresh_db()
    try:
        summary = portability.import_data(dest.conn, payload, embedder=StubEmbedder())

        assert summary["memories_imported"] == 3
        assert summary["embeddings_written"] == 3
        assert _count_embeddings(dest.conn) == 3
        # Nothing is left for the startup drip to pick up.
        assert embedding_sync.unembedded_ids(dest.conn, StubEmbedder()) == []
    finally:
        dest.close()


def test_import_without_an_embedder_still_succeeds(db: Database, config: Config) -> None:
    """Embeddings are an enhancement, never a precondition for a restore."""
    payload = _seed(db, config)

    dest = _fresh_db()
    try:
        summary = portability.import_data(dest.conn, payload, embedder=None)

        assert summary["memories_imported"] == 3
        assert summary["embeddings_written"] == 0
        assert _count_embeddings(dest.conn) == 0
        # Still keyword-reachable, and still eligible for the backfill later.
        assert len(embedding_sync.unembedded_ids(dest.conn, StubEmbedder())) == 3
    finally:
        dest.close()


def test_a_failing_embedder_does_not_cost_us_the_import(db: Database, config: Config) -> None:
    """The memories are the payload; the vectors are a bonus. Never trade one
    for the other - the write is committed before encoding is attempted."""
    payload = _seed(db, config)

    dest = _fresh_db()
    try:
        summary = portability.import_data(dest.conn, payload, embedder=BrokenEmbedder())

        assert summary["memories_imported"] == 3
        assert summary["embeddings_written"] == 0
        assert dest.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3
    finally:
        dest.close()


def test_replaced_memories_are_re_embedded(db: Database, config: Config) -> None:
    """`on_conflict="replace"` rewrites the row, so the vector must follow it.
    A stale vector for replaced text is worse than none: it is confidently wrong."""
    payload = _seed(db, config, n=2)

    dest = _fresh_db()
    try:
        portability.import_data(dest.conn, payload, embedder=StubEmbedder())
        dest.conn.execute("DELETE FROM memory_embeddings")

        summary = portability.import_data(
            dest.conn, payload, on_conflict="replace", embedder=StubEmbedder()
        )

        assert summary["memories_replaced"] == 2
        assert summary["embeddings_written"] == 2
        assert _count_embeddings(dest.conn) == 2
    finally:
        dest.close()


def test_skipped_memories_are_not_embedded(db: Database, config: Config) -> None:
    """A skip wrote nothing, so it must not claim to have embedded anything."""
    payload = _seed(db, config, n=2)

    dest = _fresh_db()
    try:
        portability.import_data(dest.conn, payload, embedder=StubEmbedder())
        dest.conn.execute("DELETE FROM memory_embeddings")

        summary = portability.import_data(dest.conn, payload, embedder=StubEmbedder())

        assert summary["memories_skipped"] == 2
        assert summary["embeddings_written"] == 0
    finally:
        dest.close()


def test_embed_ids_finishes_the_list_rather_than_one_batch(
    db: Database, config: Config, namespaces: NamespaceManager
) -> None:
    """The distinction from `backfill`, and the reason importing needed its own
    entry point. `backfill` drains one batch per call by design (it runs at
    startup, where a cold model download must not block). A caller that just
    wrote N rows knows its own ids and must not have to poll."""
    ns = namespaces.get_or_create("bulk")
    store = MemoryStore(db.conn, embedder=StubEmbedder())
    ids = [
        store.create(namespace_id=ns.id, type=MemoryType.FACT, title=f"m{i}", content="wal body").id
        for i in range(70)
    ]
    db.conn.execute("DELETE FROM memory_embeddings")

    # One backfill call clears a single batch...
    assert embedding_sync.backfill(db.conn, StubEmbedder(), batch_size=32) == 32

    db.conn.execute("DELETE FROM memory_embeddings")
    # ...while embed_ids clears all 70 despite the same batch size.
    assert embedding_sync.embed_ids(db.conn, StubEmbedder(), ids, batch_size=32) == 70
    assert _count_embeddings(db.conn) == 70


def test_imported_memory_is_reachable_by_meaning_not_just_keyword(
    db: Database, config: Config
) -> None:
    """The user-visible point of the fix, asserted through the retrieval layer."""
    payload = _seed(db, config, n=2)

    dest = _fresh_db()
    try:
        portability.import_data(dest.conn, payload, embedder=StubEmbedder())
        store = MemoryStore(dest.conn, embedder=StubEmbedder())

        vectors = store.get_embeddings_for(
            [r["id"] for r in dest.conn.execute("SELECT id FROM memories").fetchall()]
        )

        assert len(vectors) == 2
        assert all(len(v) == StubEmbedder.dim for v in vectors.values())
    finally:
        dest.close()
