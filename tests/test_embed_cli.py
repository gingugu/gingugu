"""``gingugu embed``: run the embedding backfill to completion, once."""

from __future__ import annotations

import sqlite3

import pytest

from gingugu import embed_cli
from gingugu.database import Database
from gingugu.embeddings import NullEmbeddingProvider
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore
from tests.test_chunking import NoTokenizerEmbedder, TruncatingEmbedder


@pytest.fixture
def brain(tmp_path, monkeypatch, config):
    path = tmp_path / "brain.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(path))
    conn = Database(path).connect()
    ns = NamespaceManager(conn, config).get_or_create("proj").id
    # Written by a backend with no tokenizer: head vectors, no pieces - the shape
    # of every store that predates pieces.
    old = MemoryStore(conn, embedder=NoTokenizerEmbedder())
    for i in range(70):
        old.create(
            namespace_id=ns, type=MemoryType.FACT, title=f"m{i}", content="alpha " * 12 + "delta"
        )
    conn.close()
    return path


def _unpieced(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM memories m WHERE NOT EXISTS "
            "(SELECT 1 FROM memory_chunks c WHERE c.memory_id = m.id AND c.chunk = 0)"
        ).fetchone()[0]
    finally:
        conn.close()


def test_embed_finishes_the_whole_store_not_one_batch(brain, monkeypatch, capsys):
    monkeypatch.setattr(embed_cli, "build_provider", lambda **_: TruncatingEmbedder())
    assert _unpieced(brain) == 70
    assert embed_cli.main(["--batch-size", "32"]) == 0  # 70 needs three batches
    assert _unpieced(brain) == 0
    assert "70 memories" in capsys.readouterr().out


def test_embed_is_idempotent(brain, monkeypatch, capsys):
    monkeypatch.setattr(embed_cli, "build_provider", lambda **_: TruncatingEmbedder())
    embed_cli.main([])
    capsys.readouterr()
    assert embed_cli.main([]) == 0
    assert "0 memories" in capsys.readouterr().out


def test_embed_with_embeddings_disabled_says_so(brain, monkeypatch, capsys):
    monkeypatch.setattr(embed_cli, "build_provider", lambda **_: NullEmbeddingProvider())
    assert embed_cli.main([]) == 1
    assert "disabled" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--batch-size"], ["--batch-size", "0"], ["extra"]])
def test_embed_rejects_bad_arguments(args, capsys):
    assert embed_cli.main(args) == 2
    assert "Usage" in capsys.readouterr().err
