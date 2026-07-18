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
memory_recall(query, namespace | "ns1,ns2,…", filters)
  → search.py: independent BM25 (FTS5) + semantic (cosine) candidate pools,
    RRF-fused over their union; semantic-only matches join above a 0.55
    similarity floor (≤ limit/2 entrants), BM25 candidates never displaced
  → multi-namespace: one ranked SQL pass over all listed namespaces
    (IN clause); limit caps the TOTAL list (unlike context's per-namespace limit)
  → blend with recency + confidence + access frequency
  → if include_related: hub-dampened neighbourhood appended (via_relation=true) —
    ≤3 neighbours per seed (confidence, then low degree, then recency), ≤10 total
  → ranked list; every memory stamped with its home namespace
    (compact=true: title + ~200-char summary instead of full content,
    related extras compacted too - keeps broad recalls under MCP clients'
    tool-result token caps; access is still credited)
```

`memory_search` takes the same namespace forms (single, CSV, or omitted =
all namespaces). Unknown namespaces error and name the missing one(s) — reads
never mint namespaces. Single-namespace-only tools return a comma-hint when
handed a CSV value, and `memory_store` rejects CSV outright rather than
minting a junk namespace named `"a,b"`.

## Context (session priming)

```
memory_context(namespace | "ns1,ns2,…", task_hint, limit, compact)
  → context.py selects top-N per namespace by relevance-to-hint + value signals
  → multi-namespace calls de-dupe across loads (highest-scoring instance wins);
    each memory is stamped with its home namespace
  → spreading activation wakes related dormant memories
  → returns the working set the agent should hold for the session
    (compact=true: title + ~200-char summary instead of full content)
```

Context loads are protocol-driven reads: they refresh `last_accessed` (dormancy
clock, via `touch_many`) but do **not** bump `access_count` or write
`access_log` rows - those are reserved for `memory_recall`/`memory_search`
hits, so session-start loads can't inflate the access ranking signal.

## Relations + spreading activation

```
memory_relate(source_id, target_id, relation_type)
  → relations.py writes a directed typed edge
  → later recall/context traverse edges so one hit surfaces its cluster
```

Edges are the load-bearing structure: recall quality scales with how aggressively
they are built. Store-then-relate is the expected loop.

Traversal is hub-dampened (`RelationManager.dampened_neighbour_ids`): the same
budgeted set powers `include_related` extras and spreading activation, so a
highly-connected "generic hub" memory contributes its few best neighbours
instead of its entire cluster. Budgets (3 per seed, 10 total) are tuned against
the real-brain benchmark.

## Lifecycle

- `memory_update` — mutate an existing memory (re-runs hint checks on title/content change).
- `memory_forget` — the ONLY removal path (deprecate or hard-delete). Nothing is
  auto-forgotten.
- `memory_consolidate` - merge / summarize / deduplicate a cluster; without
  `memory_ids`, a read-only suggest scan surfaces near-dupe clusters
  (pairwise embedding cosine, title-only fallback) to feed back in.
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

## Promotion (local → central)

```
gingugu promote (promote.py — an MCP client, not the server)
  → memory_export(source_ns) from the LOCAL brain (read-only)
  → filter: keep verified, minus episodic/personal tags, minus secret-content
  → memory_export(target_ns) from CENTRAL → collect already-promoted source ids
  → memory_store each fresh one into CENTRAL with a provenance stamp
     (metadata.promoted_from{instance,namespace,id,contributor,promoted_at}
      + `promoted` tag + source="promotion:<ns>")
  → idempotent: re-runs skip ids already present
```

Stage 1 = insert + skip-already-promoted. Stage 2 (consolidate near-dupes into
one canonical memory with `contributors[]`) and Stage 3 (conflict → `contradicts`
edges via an LLM judge) layer on later.

## Release

```
git tag vX.Y.Z → GitHub Actions → build → Trusted Publishing (OIDC) → PyPI
  → GitHub Release auto-cut from CHANGELOG [Unreleased]
```
