# Data Flow

## Store

```
memory_store(content, title, type, namespace, tags, confidence)
  → handlers/memory.py validates + defaults (confidence="inferred" if unset)
  → storage.py inserts into `memories`
  → FTS5 trigger mirrors the row into the full-text index
  → embeddings.py computes the semantic vector
  → dedupe/relation check → returns { ok, memory, similar_memories[], suggested_relations[] }
```

`similar_memories` (score ≥ 0.5) = merge candidates. `suggested_relations`
(score ≥ 0.3, excludes self + already-linked + items already in
`similar_memories`) = link candidates. Both are hints; neither blocks the write.

## Recall

```
memory_recall(query, namespace, filters)
  → search.py: BM25 (FTS5) lexical score  ⊕  semantic similarity (embeddings)
  → blend with recency + confidence + access frequency
  → if include_related: spreading activation pulls linked memories (via_relation=true)
  → ranked list
```

## Context (session priming)

```
memory_context(namespace, task_hint, limit)
  → context.py selects top-N by relevance-to-hint + value signals
  → spreading activation wakes related dormant memories
  → returns the working set the agent should hold for the session
```

## Relations + spreading activation

```
memory_relate(source_id, target_id, relation_type)
  → relations.py writes a directed typed edge
  → later recall/context traverse edges so one hit surfaces its cluster
```

Edges are the load-bearing structure: recall quality scales with how aggressively
they are built. Store-then-relate is the expected loop.

## Lifecycle

- `memory_update` — mutate an existing memory (re-runs hint checks on title/content change).
- `memory_forget` — the ONLY removal path (deprecate or hard-delete). Nothing is
  auto-forgotten.
- `memory_consolidate` — merge / summarize / deduplicate a cluster.
- `memory_export` / `memory_import` — back up or transfer a namespace (export
  before any large destructive op).
- `decay.py` — recomputes dormancy as a resting signal; never mutates confidence.

## Storage / migrations

```
database.py on startup:
  → open SQLite (WAL mode)
  → read PRAGMA user_version
  → apply pending migrations in order (additive by default)
  → ensure FTS5 virtual table + sync triggers exist and match `memories`
```

A schema change to `memories` MUST update the FTS5 triggers in the same change,
or full-text search silently drifts out of sync.

## Credentials

```
credential_store / credential_get / credential_list / credential_delete
  → credentials.py reads/writes secret values in the OS keychain
  → only non-secret metadata is listed; secret values never touch the DB, files, or logs
```

## Release

```
git tag vX.Y.Z → GitHub Actions → build → Trusted Publishing (OIDC) → PyPI
  → GitHub Release auto-cut from CHANGELOG [Unreleased]
```
