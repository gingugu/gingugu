# Data Flow

## Store

```
memory_store(content, title, type, namespace, tags, confidence)
  → handlers/memory.py validates + defaults (confidence="inferred" if unset)
  → storage.py inserts into `memories`
  → FTS5 trigger mirrors the row into the full-text index
  → embeddings.py computes the semantic vector
  → claim_sync.sync() derives state claims from title+content into `memory_claims`
  → dedupe/relation check → returns { ok, memory, similar_memories[],
                                      suggested_relations[], contradicted_memories[]? }
```

`similar_memories` (score ≥ 0.5) = merge candidates. `suggested_relations`
(score ≥ 0.3, excludes self + already-linked + items already in
`similar_memories`) = memories to **examine for a directional relationship**.
Both are hints; neither blocks the write.

`suggested_relations` is deliberately _not_ a link list. Overlap is how a
candidate is found; what justifies an edge is a fact similarity cannot see
(`supersedes`, `contradicts`, `caused_by`, `parent_of`/`child_of`). Recall
already ranks by hybrid text + semantic score, so a `related_to` edge meaning
"same topic" duplicates the index and — because spreading activation caps at 3
neighbours per seed and ignores `relation_type` — crowds out an edge that
carries signal. Measured 2026-08-04 before the guidance was reversed: 69% of a
real brain's 1369 edges were `related_to`.

Both are **always compact** (title + ~200-char `summary`), unlike the `memory`
the caller just wrote, which returns in full. A hint is a pointer: enough to
decide merge/link/ignore, with `memory_recall` one call away. Full bodies here
charged up to six memories of context to every write — measured on an
821-memory corpus, ~11,300 chars per store, frequently larger than the memory
being saved.

`contradicted_memories` is the write-time reconciliation hook: when this write
records a ref as resolved, every memory in the same namespace still asserting it
open is returned. **Omitted rather than empty** when there is nothing to report —
an always-present empty list is noise in every response, and this hint has to
stay cheap to ignore. Claim extraction is best-effort: a failure logs and is
swallowed so it can never break a write.

Claims are derived from text, so `memory_update` re-syncs them whenever title or
content changes — but NOT on a confidence/tag/type-only update, matching the
embedding re-encode condition.

That same "did the text move?" test also advances `last_confirmed`: **a rewrite
is a confirmation.** Someone re-read the claim and restated it. Retypes, tag
edits, metadata writes and no-op updates assert nothing about truth and leave
the clock alone. Before this, `last_confirmed` only ever moved when a caller
explicitly passed `confidence="verified"`, so ordinary content maintenance never
registered and the freshness signal — which drives scoring, the spread-neighbour
sort, staleness and `age` — silently rotted. Accepted trade: a one-word typo fix
also resets the staleness clock.

## Reconcile

```
memory_stats(review_limit=100)     → claims { open, resolved, contradicted, sample[] }
memory_search(ids="…")             → full bodies of the flagged memories
memory_update(resolve_claims="…")  → marks claims resolved; PROSE UNTOUCHED
```

`resolve_claims` takes comma-separated refs or `"all"`. It writes
`resolved_state`/`resolved_by`/`resolved_at` on the claim row and leaves the
memory body byte-identical, because a dated record that said "PR #10 open" was
accurate when written. Use `content` only when a memory asserts something that
was never true. No new tool was added for this loop — it reuses the v0.8.0
fetch-by-ids sweep.

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
  → every memory carries a derived `age` ("2 days ago"), computed at
    serialization and never stored - kept in compact mode even though raw
    timestamps are dropped. Anchored on the freshness anchor, so a memory
    maintained since it was written reads "7 weeks ago (updated just now)"
    instead of looking 7 weeks stale
```

`memory_search` takes the same namespace forms (single, CSV, or omitted =
all namespaces). Unknown namespaces error and name the missing one(s) — reads
never mint namespaces. Single-namespace-only tools return a comma-hint when
handed a CSV value, and `memory_store` rejects CSV outright rather than
minting a junk namespace named `"a,b"`.

## Context (session priming)

```
memory_context(namespace | "ns1,ns2,…", task_hint, limit, compact)
  → pinned memories load FIRST, unranked and additive to limit (cap 20/ns)
  → context.py selects top-N per namespace by relevance-to-hint + value signals
  → multi-namespace calls de-dupe across loads (highest-scoring instance wins);
    each memory is stamped with its home namespace
  → spreading activation wakes related dormant memories
  → returns the working set the agent should hold for the session
    (compact=true: title + ~200-char summary instead of full content,
    plus the derived `age` - the protocol mandates compact here, so dropping
    every temporal signal left the agent time-blind while reading the
    RESUME memory)
```

Context loads are protocol-driven reads: they refresh `last_accessed` (dormancy
clock, via `touch_many`) but do **not** bump `access_count` or write
`access_log` rows - those are reserved for `memory_recall`/`memory_search`
hits, so session-start loads can't inflate the access ranking signal.

A consequence worth knowing: because the protocol calls `memory_context` every
session and that refreshes the dormancy clock, anything it routinely surfaces
can never accumulate `DORMANT_AFTER_DAYS` untouched. Dormancy only ever reaches
the tail. Intended, and now pinned by `tests/test_dormancy_lifecycle.py`.

**Pinned memories** sit outside all of this. Ranking answers "what is most
relevant to this task?"; it cannot answer "what must never be missing?". A pin
(`memory_update(pinned=True)`) removes a memory from the ranking contest
entirely - it is not scored, not quota'd, and not evictable - and is returned
_in addition to_ `limit`, because a tier that truncates under contention
recreates the failure it exists to fix. Bounded by `PINNED_HARD_CAP = 20` per
namespace at the write path instead. Deprecation beats a pin.

## Relations + spreading activation

```
memory_relate(source_id, target_id, relation_type)
  → relations.py writes a directed typed edge
  → later recall/context traverse edges so one hit surfaces its cluster
```

Edges are the load-bearing structure, but recall quality scales with edge
**precision**, not edge count. Hybrid retrieval already ranks by text and
semantic similarity, so a `related_to` edge meaning "these are about the same
topic" duplicates the index for free while competing for a traversal slot
against an edge that records what search cannot infer: what a memory
`supersedes`, `contradicts`, was `caused_by`, or belongs under. Store-then-relate
is still the expected loop; the filter is whether you can name the directional
fact the edge records.

Measured on the live brain 2026-08-13 via the new `memory_stats.graph` block:
61% of 1,570 edges are `related_to` and 339 memories carry more edges than
traversal will ever visit - the cost of the older volume framing, still being
worked off.

Traversal is hub-dampened (`RelationManager.dampened_neighbour_ids`): the same
budgeted set powers `include_related` extras and spreading activation, so a
highly-connected "generic hub" memory contributes its few best neighbours
instead of its entire cluster. Budgets (3 per seed, 10 total) are tuned against
the real-brain benchmark.

## Lifecycle

- `memory_update` — mutate an existing memory (re-runs hint checks on title/content
  change). Also retypes: `type` is the fix for a misfiled memory, since `pattern`
  and `preference` are exempt from gated review hints. Retyping does not re-embed.
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

A migration that adds **derived** data must also populate it, or the feature
ships inert for everyone who already has memories. Migration 005 backfills
claims inline (pure regex, ~210ms for 735 memories, and `user_version` makes it
exactly-once); migration 004 backfills embeddings at *startup* instead because
encoding needs a model download and must stay lazy. See
`.ai/standards/02-database.md` for which strategy applies when.

A migration that changes how derived data is **computed** must re-derive it,
and must decide explicitly what happens to state a user added on top. Migration
007 re-derives every claim through `claim_rederive.py`, which prunes claims the
corrected extractor no longer produces while preserving `resolved_*` — because
the prose did not change, only the extractor improved, and reconciliation work
is manual and unrecoverable. Reusing `claim_sync.sync_claims` here would have
silently reopened every claim the user had reconciled.

Exactly-once cuts both ways: pending work is selected with `current < target`,
so a migration can never re-run on a DB that already passed it, and a bug fixed
in place reaches only DBs that have not got there yet. Repairing the rest needs
a **new** version number — migration 006 exists solely to re-run the claims
backfill for DBs stamped v5 by a build whose 005 did not have one.

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
