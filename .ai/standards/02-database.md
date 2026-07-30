# Standards: Database Discipline

The SQLite database is the product's durable state. Treat it with care.

## Location

- Default path resolved by `config.py` via **platformdirs** (e.g.
  `~/.local/share/gingugu/memories.db` on Linux; `%LOCALAPPDATA%` on Windows).
- **Never** place the DB inside the repo. Never commit `*.db`.

## Concurrency

- **WAL mode always** (`PRAGMA journal_mode=WAL`) for concurrent reads while a
  write is in flight (e.g. the UI reading while the server writes).

## Schema changes = migrations

- Keyed off **`PRAGMA user_version`**. Bump it and apply the migration in
  `database.py` on startup, in order.
- **Additive by default.** Destructive migrations (dropping/renaming columns,
  deleting rows) require explicit user approval.
- **A migration that adds derived data MUST populate it.** If the new table or
  column is derived from data that already exists, creating it empty ships a
  feature that does nothing until the user happens to rewrite every row. State
  the backfill strategy in the PR body — "none needed" is a valid answer, but
  it has to be a stated one.

  Two valid strategies, and the choice is about **cost**, not preference:

  | | Where | When to use |
  |---|---|---|
  | In the migration | `_migration_00N()` | Cheap, pure, no I/O. `user_version` guarantees exactly one run. Example: claims (regex over existing text, ~210ms for 735 memories). |
  | At startup | `server.py` after `MemoryStore` | Expensive or failure-prone — needs batching, network, or a model. Example: embeddings (~80MB model download, so it must stay lazy). |

  A startup backfill needs a reliable "already processed" marker. "Row has no
  child records" is **not** one when a row can legitimately produce zero — most
  memories contain no PR reference, so a claims backfill keyed that way would
  rescan the whole corpus on every boot forever. Embeddings can use it only
  because every memory should have exactly one.

## A shipped migration can never be fixed in place

`migrate()` selects pending work with `current < target`. Once a DB is stamped
version N, migration N will **never run again on that DB** — so editing
migration N to fix a bug reaches only DBs that have not yet passed it. Every
DB already at N is stranded, permanently, and no reinstall or restart helps.

**Fixing a migration that any real DB has already applied requires a NEW
version number.** Write the repair as migration N+1 (see 006, which re-runs
the claims backfill for DBs that reached v5 from pre-fix code). Make the
repair idempotent — `INSERT OR IGNORE` against a UNIQUE constraint, never a
DELETE-then-reinsert, which would clobber state the user has since changed —
and run it **unconditionally** rather than guarding on "looks unprocessed". A
stranded DB accumulates partial data through normal use, so a guard like "the
table is empty" stops recognising it.

### The rule that prevents this

**Never point in-development schema code at a live database.** A dev server or
an ad-hoc script running an unmerged migration stamps `user_version` for real,
and the fix you write afterwards is then unreachable on the machine you are
developing on. Point dev instances at a throwaway copy (`MEMORY_DB_PATH`), and
check `PRAGMA user_version` on the live file *before* declaring a migration
path verified — validating against DB copies proves nothing about a live file
that has already moved on.

## FTS5 in lockstep

- The `memories` table is mirrored into an **FTS5** virtual table by sync
  triggers. Any change to `memories` (new/renamed searchable column) MUST update
  those triggers in the same change — otherwise full-text search silently drifts.

## Never forget

- `decay.py` computes dormancy as a **resting signal** only. It must never
  mutate confidence or delete rows. Do not reintroduce time-based decay.
- The only removal path is explicit `memory_forget`.

## Backups before destructive ops

- Copy the DB file (or `memory_export` the namespace) before any
  `memory_consolidate` / prune touching **>100 rows**.

## Integrity

- Relations are directed typed edges; avoid duplicate edges and self-loops.
- Validate/normalize any file path derived from user content (no traversal).
