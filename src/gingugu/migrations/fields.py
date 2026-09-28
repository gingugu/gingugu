"""Write-time declared fields: ``provenance`` and ``about``.

Two columns the writer fills in, alongside what it wrote:

* ``provenance`` - how the writer came to believe the memory. A controlled
  vocabulary (``models.Provenance``), enforced by the application layer rather
  than a ``CHECK`` constraint: SQLite cannot alter a constraint in place, so a
  vocabulary change would otherwise mean rebuilding ``memories`` and every
  trigger on it. ``source`` is left alone; measured on a real brain it records
  the *occasion* a memory was written, which is a different axis.
* ``about`` - what the memory is for, in the user's words. It is only useful if
  retrieval can see it, so it joins the FTS5 index here and the embedding text
  in ``embedding_text.embedding_input``.

Both start NULL on every existing row. Nothing is backfilled: a provenance
guessed from free text would be exactly the model-free-but-still-invented
judgment the design law forbids.
"""

from __future__ import annotations

import sqlite3

# Recreating an external-content FTS5 table does not touch ``memories``; the
# 'rebuild' command then re-reads every row through the new column list.
# Wrapped in one transaction so an interrupted run leaves the old index intact.
_FTS_V14 = """
BEGIN;
DROP TRIGGER IF EXISTS memories_ai;
DROP TRIGGER IF EXISTS memories_ad;
DROP TRIGGER IF EXISTS memories_au;
DROP TABLE IF EXISTS memories_fts;

CREATE VIRTUAL TABLE memories_fts USING fts5(
    title,
    content,
    about,
    content=memories,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, about)
    VALUES (new.rowid, new.title, new.content, new.about);
END;

CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, about)
    VALUES ('delete', old.rowid, old.title, old.content, old.about);
END;

CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, about)
    VALUES ('delete', old.rowid, old.title, old.content, old.about);
    INSERT INTO memories_fts(rowid, title, content, about)
    VALUES (new.rowid, new.title, new.content, new.about);
END;

INSERT INTO memories_fts(memories_fts) VALUES('rebuild');
COMMIT;
"""


def _migration_014_provenance_about(conn: sqlite3.Connection) -> None:
    """Add ``provenance`` and ``about``; rebuild FTS5 with ``about`` indexed.

    Safe to re-run. ``migrate`` stamps ``user_version`` only after this returns,
    so a crash in between runs it again on the next start - over columns that
    already exist, which a bare ``ADD COLUMN`` would refuse.
    """
    existing = {row[1] for row in conn.execute("SELECT * FROM pragma_table_info('memories')")}
    for column in ("provenance", "about"):
        if column not in existing:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {column} TEXT")
    conn.commit()
    conn.executescript(_FTS_V14)
