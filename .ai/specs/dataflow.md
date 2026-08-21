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

`similar_memories` = merge candidates. `suggested_relations` (excludes self +
already-linked + items already in `similar_memories`) = memories to **examine
for a directional relationship**. Both are hints; neither blocks the write.

Both are **two-stage**, and the stages answer different questions:

1. **Find** with hybrid retrieval (`search.py`). RRF fusion is good at "what is
   nearest".
2. **Adjudicate** with an absolute measure (`similarity.py`) and gate on that:
   cosine over the stored embeddings, or token Jaccard when embeddings are
   unavailable. Each hit reports `similarity` + the `basis` it was measured on,
   and no retrieval `score`.

Stage 2 is not optional garnish. The fused RRF relevance is a function of a
candidate's **rank** in the pools, normalized so rank 1 in both maps to 1.0,
and something is always rank 1, so the top hit trended toward 1.0 for every
payload ever written, while both gates (0.5 and 0.3) sat below what the
arithmetic could even produce. Measured on a 1,423-memory brain: the payload
"Lunch was a tuna sandwich" scored **0.9262** against a corpus of engineering
notes, the identical score two other unrelated payloads got in a different
namespace. The practical cost was three merge candidates and three relation
candidates on **every** store, each needing a manual read to dismiss.

Cutoffs are calibrated, not guessed: 228 `supersedes` pairs as positives
against 7,688 random same-namespace pairs. Cosine `0.80` and Jaccard `0.15` sit
at the same operating point (~8.5% of random pairs admitted, ~85% of genuine
near-duplicates kept), so turning embeddings off changes the instrument's
precision but not the meaning of the gate. Relations use a softer `0.72`/`0.10`:
they are candidates to examine, not merge proposals. Do not port these numbers
to another corpus unexamined; BGE cosine does not bottom out near zero, and two
unrelated memories from one brain sit around 0.71 simply from shared register.

An empty hint list is the **common** case and the signal working.

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
memory_stats(review_limit=100)     → claims { open, open_actionable, resolved,
                                              unverified, contradicted, sample[] }
memory_search(claims="open")       → full bodies of the backlog (or ids="…")
memory_search(claims="unverified") → refs named but never resolved in prose
memory_update(resolve_claims="…")  → marks claims resolved; PROSE UNTOUCHED
```

`resolve_claims` takes comma-separated refs or `"all"`. It writes
`resolved_state`/`resolved_by`/`resolved_at` on the claim row and leaves the
memory body byte-identical, because a dated record that said "PR #10 open" was
accurate when written. Use `content` only when a memory asserts something that
was never true. No new tool was added for this loop — `memory_search` gained a
`claims` filter, and the v0.8.0 fetch-by-ids sweep still works.

**`sample` enumerates, it does not illustrate.** It first listed only the
_contradicted_ subset — defensible (those are answerable from what the brain
already holds) and unusable in practice: a namespace could report five open
claims and give the caller no way to learn which five, so a real sweep dropped
to raw SQL against the live database. It now lists every open claim,
contradicted ones first, each row tagged `contradicted`.

Two counts because they differ: `open` is every unresolved claim, while
`open_actionable` — what `sample` lists — excludes claims on deprecated
memories. Reporting only `open` invites a caller to compare it against
`len(sample)` and conclude rows went missing.

**`unverified` is a third state, not a third slice of the backlog.** A ref the
prose names without ever saying what became of it used to produce no claim at
all, so `claims.open` read 0 while the memory read as in-flight forever. The
obvious repair — call it open — was measured and rejected: 185 such claims exist
against 223 real ones, and nearly all narrate work that already shipped, so
adopting them would have buried the backlog in history. It is therefore excluded
from `open`, `open_actionable`, `sample`, and contradiction detection (which
requires two assertions, and silence is not one), and read on its own through
`memory_search(claims="unverified")`. `resolve_claims="all"` stays open-only for
the same reason; naming the ref explicitly still resolves it.

`claim_queries.claim_filter()` is the single definition of both predicates,
shared by the stats block and `memory_search(claims=…)` through
`search_common.build_filters`. It contributes no bound parameters, so it
composes with the FTS5 join, the embeddings join, and a plain table scan
alike. Contradiction stays scoped **within** a namespace — bare refs are keyed
off the namespace's default repo, so cross-namespace matching would pair two
different repos' `PR #12`.

## Recall

```
memory_recall(query, namespace | "ns1,ns2,…", filters)
  → search.py: independent BM25 (FTS5) + semantic (cosine) candidate pools,
    RRF-fused over their union; semantic-only matches join above a 0.55
    similarity floor (≤ 5 entrants), cohort members never displaced
  → the semantic cohort is a FIXED 40, never scaled by limit: a rank only
    means something against a fixed cohort, so sizing it by the caller's row
    count made relevance move with `limit`. `search(q, k)` is now exactly the
    first k of `search(q, K)`. Ties break on id, not on set iteration order
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

`memory_search`'s `sort_by` selects the retrieval path rather than reordering
one:

```
sort_by = relevance | decay_score
  → with a query:    search.py, the hybrid engine above
  → without a query: score every matching row (six columns, no bodies), take
                     the top `limit`, then fetch those bodies by id
sort_by = created | accessed
  → with a query:    the FTS match set, ORDER BY the column, LIMIT in SQL -
                     no BM25 ranking and no semantic cohort, because a date
                     asks what relevance cannot answer. No `score` returned
  → without a query: ORDER BY the column, LIMIT in SQL
ids = "a,b,c"
  → exact fetch, requested order, deprecated included, `missing` reported
```

Every path selects its rows in the order it returns them. Sorting a pool that
was truncated on a different axis reorders a biased sample rather than the
corpus, so a row that lost the earlier cut is unreachable however well it
matches the sort - which is why `sort_by="created"` used to return neither the
newest rows nor a stable answer as `limit` changed. Ties break on `id`.

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
  → selection order ≠ presentation order (see below)
  → multi-namespace calls de-dupe across loads (highest-scoring instance wins);
    each namespace's pins lead, then the ranked tails interleave by rank;
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

**Selection order is not presentation order**, and conflating them is what put
the pinned tier at the bottom of every payload. Selection fills the recency
quota _first_, because filling it first is what stops a contended `limit` from
evicting the "where we left off" memory - that is a survival question.
Presentation then answers a different one, "what should the agent read first?",
and emits by bucket membership: task hits (the caller asked a question), then
recency, then cross-namespace, then the score-ordered backfill. Membership, not
which quota happened to claim the row - recency is filled first, so a
task-relevant memory that is also recent would otherwise be presented as though
it had never matched the query.

Neither layer re-sorts by composite score, because that score is not comparable
across buckets: only the task bucket has a real search relevance, while the
recency and cross-namespace buckets carry a fixed `relevance=0.5` placeholder
(they have no query to be relevant _to_). Sorting on it ranks rows against each
other on a scale none of them share - and silently undoes the quota that just
protected the recency slot. Measured on a copy of the live brain, the old global
sort placed **0 of 8** pins in the top 8; the fix places **8 of 8**.

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

memory_edges(namespace|relation_type|memory_id)     → read the graph
memory_unrelate(... new_relation_type? reverse?)    → repair it
  | edges[]
memory_stats.graph.orphan_sample                    → what the graph never reaches
memory_search(orphans=True)                         → the same set, with bodies
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

Edges are repairable, which is what makes that precision demand fair: a surface
that insists on picking a specific type and then makes every wrong pick
permanent charges the full cost of an error on every future recall.
`memory_edges` reads the graph (both endpoints resolved to titles, namespaces
and degree, so an edge can be judged from the row) and `memory_unrelate`
repairs it - retype in place, reverse, or delete. Retype UPDATEs the row rather
than recreating it, so id, `created_at` and metadata survive: the usual repair
is "right connection, wrong label", and when the link was drawn stays true.
Retyping onto a type that already joins the pair collapses the two and reports
`merged`, not `retyped` - the edge count genuinely drops by one.

`reverse` is the sibling repair: the pair is right and the arrow points the
wrong way. `A caused_by B` written for `B caused_by A` is a false claim about
causality, not an untidy one, and the directional types this graph asks for are
exactly the ones that can be recorded backwards. It swaps the endpoints on the
same row, with the same provenance guarantee, and combines with a retype in one
write because an edge recorded backwards is frequently mislabelled too.
Reversing `parent_of`/`child_of` is the same operation as flipping between
them - do one or the other, not both.

Batches are lists of per-edge decisions, deliberately **not** criteria-driven
sweeps. There is no "retype every `related_to` here" option, because each edge
deserves a different type based on what it records; a blanket relabel would
manufacture directional claims that were never true, and a false `caused_by`
retrieves worse than an honest `related_to`. The batch is validated whole before
anything is written, so a bad op cannot leave the graph half-repaired.

Traversal is hub-dampened (`RelationManager.dampened_neighbour_ids`): the same
budgeted set powers `include_related` extras and spreading activation, so a
highly-connected "generic hub" memory contributes its few best neighbours
instead of its entire cluster. Budgets (3 per seed, 10 total) are tuned against
the real-brain benchmark.

The other end of the same problem is memories the graph never reaches at all.
`memory_stats.graph` reports an orphan count, and `orphan_sample` names the
memories behind it - ordered by confidence, then access count, then recency, so
the orphans costing the most retrieval come first, each row carrying its
namespace. `memory_search(orphans=True)` pulls the same set with full bodies,
composing with every other filter, with or without a query.
`graph_stats.orphan_filter()` is the single definition behind both, on the same
argument as `claim_filter()`: a count and its enumeration must be counting the
same thing. Deprecated orphans sink to the bottom of the sample rather than
being filtered out, so - unlike claims, which needed `open` and
`open_actionable` - one population serves both numbers and no gap needs
explaining. Reconnecting an orphan is still a judged act: an orphan is better
left alone than wired up with an invented edge.

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
  before any large destructive op). `memory_import` embeds what it writes and
  reports `embeddings_written`; without that, restored memories were reachable
  by keyword only, because FTS5 has triggers and `memory_embeddings` does not.
  Vectors are recomputed on arrival rather than carried in the payload: they
  are model-specific, so a 384-dim export restored on a 768-dim host would be
  discarded anyway, and they are derived from the text sitting beside them.
  Encoding happens **after** the commit, so a failing model costs the vectors,
  never the restore.
- `decay.py` — recomputes dormancy as a resting signal; never mutates confidence.

## Storage / migrations

```
database.py on startup:
  → open SQLite (WAL mode)
  → read PRAGMA user_version
  → if migrations pending: snapshot to <db>.bak-before-vN via conn.backup()
  → apply pending migrations in order (additive by default)
  → ensure FTS5 virtual table + sync triggers exist and match `memories`
```

The snapshot is taken with SQLite's backup API rather than a file copy, because
in WAL mode the newest committed rows live in `<db>-wal` until a checkpoint. It
is best-effort: a failure is logged and the migration still runs.

Every read of `memories` selects `models.MEMORY_COLUMNS`, the single declared
column list, so no query can return a `Memory` with a field silently left at its
default.

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
