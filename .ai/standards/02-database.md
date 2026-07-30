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

### Re-deriving is not the same as backfilling

When a migration changes **how** derived data is computed rather than adding
it, it must re-derive — and it must state explicitly what happens to state a
user layered on top.

Backfills are additive and can be idempotent for free (`INSERT OR IGNORE`
against a UNIQUE constraint). A re-derive **removes** rows, so it needs to
answer: what about the rows carrying user-supplied state?

The dividing question is *what changed*:

| What changed | Correct behavior |
| --- | --- |
| The memory's **prose** | Drop user state. What the text asserts may have changed with it, and a stale resolution pointer is worse than none. (`claim_sync.sync_claims`) |
| The **extractor** | Preserve user state. The text is unchanged, so any reconciliation recorded against it still holds. (`claim_rederive.rederive_claims`) |

Migration 007 got this wrong in draft: reusing `sync_claims` would have run
green through the whole test suite while silently reopening every claim the
user had reconciled by hand — work that is manual, unlogged, and unrecoverable.
**Before reusing an existing sync path inside a migration, check what it
deletes.** A test that only asserts the new behavior will not catch it.

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

### A setting that changes only future behavior is inert

Derived data is *stored*. A config value consulted at derivation time changes
nothing that already exists, so a setting shipped without a path to re-derive
is a setting that does nothing the user can see.

`default_repo` shipped that way in 0.11.0: setting it updated the column and
left every already-derived claim on its old key, with no supported way to apply
it — the re-derive was migration-side only, and `storage.update` re-syncs claims
only when the prose actually changed. The only remaining route was editing
memory text, which is the exact dodge the claims design exists to eliminate.

**Before shipping a setting that feeds a derivation, answer: what happens to
rows derived before it was set?** "Nothing" is a valid answer only if you say so
in the docs. Otherwise the write path that changes the setting owns the
re-derive.

It also means the test that would have caught it is the one nobody writes: the
migration path was covered because migration 007 re-derives inside its own
body, so the seeded namespaces worked. The *user* path — set the value on an
already-populated namespace afterwards — never ran end to end.

### Copy a WAL database with the backup API, never `shutil.copy`

Rehearsing on a copy of the real DB is the right discipline, but the copy has
to be of the *current* state. Gingugu runs SQLite in **WAL mode**, so recent
commits live in `memories.db-wal` until a checkpoint folds them in.
`shutil.copy` takes only `memories.db` and silently produces a stale snapshot.

```python
src = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)  # read-only: cannot touch the real brain
dst = sqlite3.connect(COPY)
src.backup(dst)                                          # checkpoints WAL content for us
```

Delete any stale `COPY-wal` / `COPY-shm` first, or the new copy inherits them.

This bit during the migration 007 rehearsal: the stale copy reported that the
migration was wiping ten hand-reconciled claims. It was not — the copy predated
the reconciliation. **When a rehearsal contradicts work you did minutes ago,
suspect the harness before the code.**

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
