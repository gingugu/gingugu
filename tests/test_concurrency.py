"""Concurrency tests — the real production model is several Windsurf windows,
each its own process/connection, writing to one shared DB file under WAL.

We approximate that with multiple threads, each opening its *own* connection to
a shared on-disk database (SQLite locking is per-connection/file, not per-thread,
so this exercises the same WAL + busy_timeout machinery)."""

from __future__ import annotations

import threading
from pathlib import Path

from gingugu.config import Config
from gingugu.database import Database
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def _config(db_path: Path) -> Config:
    return Config(
        db_path=db_path,
        namespace="conc",
        namespace_path=None,
        auto_context_limit=10,
        decay_lambda=0.05,
    )


def test_concurrent_writers_all_land(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.db"
    # Initialize schema once (migrations) before fan-out.
    main = Database(db_path)
    main.connect()
    ns_id = NamespaceManager(main.conn, _config(db_path)).get_or_create("conc").id

    writers = 8
    per_writer = 25
    errors: list[Exception] = []
    barrier = threading.Barrier(writers)

    def worker(worker_id: int) -> None:
        try:
            db = Database(db_path)
            store = MemoryStore(db.connect())
            barrier.wait()  # maximize contention: everyone starts together
            for i in range(per_writer):
                store.create(
                    namespace_id=ns_id,
                    type=MemoryType.FACT,
                    title=f"w{worker_id}-m{i}",
                    content=f"content from writer {worker_id} item {i}",
                    tags=[f"w{worker_id}"],
                )
            db.close()
        except Exception as exc:  # capture, assert in main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writers raised: {errors}"
    total = main.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert total == writers * per_writer
    # FTS index stayed consistent with the base table under contention.
    fts = main.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    assert fts == writers * per_writer
    main.close()


def test_concurrent_reads_and_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "rw.db"
    main = Database(db_path)
    main.connect()
    cfg = _config(db_path)
    ns_id = NamespaceManager(main.conn, cfg).get_or_create("conc").id
    seed = MemoryStore(main.conn)
    for i in range(20):
        seed.create(
            namespace_id=ns_id, type=MemoryType.FACT, title=f"seed{i}", content="alpha beta gamma"
        )

    errors: list[Exception] = []

    def reader() -> None:
        try:
            from gingugu import search as search_mod

            db = Database(db_path)
            conn = db.connect()
            for _ in range(30):
                search_mod.search(conn, query="alpha beta", namespace_id=ns_id, limit=5)
            db.close()
        except Exception as exc:
            errors.append(exc)

    def writer() -> None:
        try:
            db = Database(db_path)
            store = MemoryStore(db.connect())
            for i in range(30):
                store.create(
                    namespace_id=ns_id,
                    type=MemoryType.FACT,
                    title=f"live{i}",
                    content="alpha delta",
                )
            db.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads += [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent read/write raised: {errors}"
    total = main.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert total == 20 + 4 * 30
    main.close()
