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
