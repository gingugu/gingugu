# Architecture

## Overview

Gingugu is a single-process **MCP server**. By default an AI client spawns it
over **stdio**; it can also run over **streamable HTTP** (`gingugu serve`, gated
by a Bearer token) so a hosted/central instance is reachable remotely. It owns
one local SQLite database and exposes a set of memory tools — the entire system
is the server process plus the DB file plus an optional local web UI.

```
AI client (Claude Code / Cursor / Windsurf / …)
        │  MCP stdio (JSON-RPC)
        ▼
  gingugu server.py  ──►  handlers/*  ──►  storage / search / relations / context
        │                                        │
        ▼                                        ▼
  config.py (DB path)                    SQLite (memories + FTS5 + relations)
                                                 ▲
                              webui.py ───────────┘  (read-mostly Memory Explorer; `gingugu ui`)
```

## Layers

1. **Transport** — `server.py` registers MCP tools and routes calls to handlers.
   It is the crash boundary: no exception escapes to the client. Two transports
   share this path: **stdio** (default) and **streamable HTTP** via `serve.py`
   (`gingugu serve`), which wraps the same server in a Starlette app with
   Bearer-token auth middleware and a `/healthz` probe. The `credential_*` tools
   are gated by `MEMORY_CREDENTIALS_ENABLED` so a shared instance can omit the
   secret vault.
2. **Handlers** (`handlers/`) — thin adapters that validate input, call the core
   modules, and return structured dicts. Split by domain: `memory`, `search`,
   `relations`, `admin`, `credentials`, plus `helpers`.
3. **Core** — `storage`, `search`, `embeddings`, `context`, `relations`,
   `consolidation`, `decay`, `stats`, `namespaces`, `portability`.
4. **Persistence** — `database.py` owns the SQLite connection, schema,
   migrations, WAL, and FTS5 triggers. `config.py` resolves the DB path.

## Memory Model

- **Two namespaces layers:** `crow` (global identity/cross-project) + one per
  project. Every memory belongs to exactly one namespace.
- **Typed memories:** `type` ∈ {fact, decision, pattern, bug, architecture,
  preference, workflow, context}; `confidence` ∈ {verified, inferred, stale,
  deprecated}.
- **Graph:** directed typed relations (`supersedes`, `related_to`, `caused_by`,
  `contradicts`, `parent_of`, `child_of`). Recall uses **spreading activation** —
  surfacing a memory wakes its linked cluster.
- **Never-forget:** `decay.py` tracks dormancy (untouched ≥ 90 days) as a
  *resting signal* only. Nothing is auto-demoted or auto-deleted; only explicit
  `memory_forget` removes a memory.
- **Derived time, never stored:** `decay.relative_age()` turns an instant into
  `"2 days ago"` at serialization. Every read surface carries it; nothing
  persists it. Storing a relative timestamp would reintroduce the exact rot
  that `memory_claims` and `review_hints` exist to detect. Precedent:
  `credentials.expiry_status()` and `staleness` classify at call time too — no
  schema column anywhere holds a derived time value.
- **One freshness anchor, and every consumer uses it.**
  `decay.reference_timestamp()` returns the **latest** of `last_confirmed`,
  `updated_at`, `created_at` — a `MAX`, not a `COALESCE`, so an edit made after
  the last confirmation is not discarded. The scorer, the spread-neighbour sort,
  staleness and the payload `age` (`decay.age_label()`) all read it; the two SQL
  call sites mirror the same rule. Feeding it: **a rewrite is a confirmation** —
  `storage.update` advances `last_confirmed` when the title or content actually
  changed, reusing the "did the matching surface move?" test that already gates
  claim re-sync and embedding re-encode. Retypes, tag edits and metadata writes
  assert nothing about truth and leave the clock alone.
- **State claims:** `claims.py` extracts repo-qualified PR/MR references and the
  state a memory asserts about them into `memory_claims` (migration 005;
  migration 006 re-runs the backfill for DBs stranded at v5; migration 007
  re-derives everything under the corrected extractor; migration 009 re-derives
  again for the `unverified` state below).
  The prose is immutable history — a memory that said "PR #10 open" was correct
  when written — so resolution is recorded in the claim row rather than by
  editing the text. This is the primitive whose absence produced 160 distinct
  ad-hoc `=== STATUS ===` banner styles across the dogfooding corpus.

  Refs are qualified by URL, then a repo named beside them, then the
  namespace's `default_repo` — unset means the namespace's own name (the
  one-namespace-per-repo convention), `""` means the namespace is not a repo
  at all. Unqualifiable refs are dropped rather than guessed, and contradiction
  detection is namespace-scoped so a bare-ref mis-key cannot reach across
  namespaces.

  Refs inside `[[wiki-links]]` are ignored: a link to a memory *titled*
  "PR #10 open" is a citation, not this memory's assertion. Namespace scoping
  does **not** contain this one — it produced wrong claims in namespaces whose
  default repo was correct — which is why it is handled in the extractor.

  A ref whose prose asserts no state records as **`unverified`** rather than
  being dropped: dropping made it invisible, and a memory listing `PR #1: <url>`
  under "Deliverables" read as in-flight forever while `claims.open` said 0.
  Calling it open instead was measured against the live corpus and rejected —
  185 such claims against 223 real ones, nearly all narrating shipped work — so
  it is excluded from `open`, `open_actionable`, `sample`, and contradiction
  detection, and surfaced only through its own filter. The governing rule is
  unchanged from the paragraphs above: a missed claim is silent, a wrong one
  teaches the reader to ignore claims entirely.

  `claim_rederive.py` re-derives the whole corpus when the *extractor* changes,
  preserving `resolved_*`. That is the opposite of `claim_sync.sync_claims`,
  which drops resolution because it runs when a memory's *prose* changed and a
  stale resolution pointer would be worse than none.

  The read side lives in `claim_queries.py`: the `memory_stats.claims` backlog
  enumeration and the `claim_filter()` predicate behind
  `memory_search(claims="open"|"contradicted"|"unverified")`. One definition, two consumers —
  a stats block and a search filter asking the same question with two
  hand-written correlated subqueries is how they drift apart. The split also
  put `claim_sync.py` back under the 300-line limit.

  `graph_stats.orphan_filter()` follows the same shape for the graph backlog:
  one predicate behind both the `memory_stats.graph.orphans` count and
  `memory_search(orphans=True)`, so a count and its enumeration cannot come to
  disagree about what they are counting. `compute_graph` returns an
  `orphan_sample` alongside the count, since a metric nothing can act on
  describes a cost without offering a way to pay it down.

  Edge repair lives in `relation_repair.py`, mixed into `RelationManager`:
  `retype_relation` and `reverse_relation` are the two halves of "the pair is
  right, the label or the arrow is not", and both UPDATE the existing row so an
  edge's provenance survives its correction. Split out when the reversal work
  pushed `relations.py` past the 300-line limit; the same pass moved the batch
  parsing and per-edge dispatch out of `handlers/relations.py` into
  `handlers/relation_ops.py`, leaving the handler owning the MCP surface alone.

## Retrieval

- `memory_recall` blends **BM25** (FTS5 lexical) with **semantic** similarity
  (embeddings), combined with recency, confidence, and access frequency.
- `memory_context` is the session-priming entrypoint: top-N by relevance to a
  task hint, plus spreading activation into related memories. Accepts a
  comma-separated namespace list (one call per session, de-duped across
  namespaces) and a `compact` mode (title + excerpt). Context loads refresh
  the dormancy clock but don't count as accesses - `access_count` is a pure
  recall/search usage signal. **Pinned** memories (`memory_update(pinned=True)`)
  load ahead of the ranked set and exempt from it, additive to `limit` and
  capped at 20 per namespace - the tier for rules that must never be missing,
  which is a different question from what ranking answers.
- **Selection order and presentation order are separate decisions** in
  `memory_context`, and neither one re-sorts by composite score. Quotas are
  filled recency-first (so a contended `limit` cannot evict the "where we left
  off" memory); the payload is then emitted by bucket membership - task hits,
  recency, cross-namespace, backfill - with pins ahead of all of it. The
  composite is not comparable across those buckets: only the task bucket
  carries a real search relevance, the others carry a `relevance=0.5`
  placeholder, and pins carry no score at all. Sorting the payload on it sank
  every pin to the bottom.
- `memory_search` is the precision path: explicit filters (tags, type, date,
  confidence), sort order, and exact fetch-by-`ids` (requested order,
  deprecated included, `missing` reported) - the companion to
  `memory_stats(review_limit=...)` for review sweeps.

## Key Decisions

- **Local-first, single file.** No server to run, no cloud dependency; the DB is
  portable and inspectable. Trade-off: no built-in multi-user sync (out of scope).
- **Optional network transport, still single-owner.** `gingugu serve` exposes
  the brain over HTTP behind one shared Bearer token for a hosted/central
  instance, but it stays a single SQLite file with no per-user RBAC —
  multi-tenant auth remains roadmap (see `docs/future-architecture.md`).
- **Promotion is a client, not server logic.** `gingugu promote` (`promote.py`)
  speaks the public MCP tool surface to two instances — read-only `memory_export`
  from a local brain, filtered `memory_store` into a central brain with a
  provenance stamp. The server gains no promotion-specific code; the selective
  local→central absorption lives entirely in the client. Keeps the store pure.
- **Onboarding is client-side too.** `gingugu init` (`bootstrap/`) writes no
  server code — it copies packaged templates (`bootstrap/templates/*.tmpl`) into
  a target repo: for Claude Code, a `SessionStart` hook that auto-injects the
  memory startup contract every session (a rules file is not guaranteed to load
  into context; a hook is), a `Stop` save-discipline hook, and the
  `/sink-the-ship` command — merging both hooks into `.claude/settings.json`
  non-destructively (`settings.py`) and appending the hooks' runtime artifacts
  (`logs/`, `.claude/data/`, `settings.local.json`) to the target's `.gitignore`
  so transcripts never get committed. Output is a themed 90s boot sequence
  (`theme.py`, degrades to monochrome off-TTY). Other clients (`--client`) get a
  rules file. This closes the gap where the repo's own hook-based install
  outperformed the copy-paste setup shipped to users.
- **The user-level rules file is part of the bootstrap, and is merged, not
  written.** `bootstrap/global_rules.py` manages the protocol inside a marked
  block in `~/.claude/CLAUDE.md` — the file loaded in *every* session, including
  directories with no project protocol installed. A repo rules file is written
  by `init`, so a `--force`-gated whole-file write is fine there **provided it
  backs the old bytes up first** - "init writes this file" was read for a while
  as "init owns this file", and the rules-file path shipped with no `.bak` at
  all until that was corrected; the
  user-level file is hand-authored and carries identity/workflow rules unrelated
  to memory, so the only bytes `init` may rewrite are the ones between its own
  sentinels. Everything else appends. An unmanaged protocol already in the file
  is a refusal plus instructions, never a silent second set of memory rules.
  There is deliberately **no `--global` flag**: making the step opt-in would
  imply the protocol is optional. It exists because that file drifted — it still
  said "build edges aggressively" long after the templates had moved on, with no
  tooling able to correct it. Nothing we ship may name that flag; a test asserts
  the managed note, the module docstring and the run output never do, because the
  note is written into the user's own `CLAUDE.md` and a stale invocation there is
  advice they will follow and watch fail.
- **A `--force` backup keys off content, never off ownership.** Whether a file
  carries `gingugu-init:managed-file` says whether *we* wrote it; it says nothing
  about whether the user has since edited it. Conditioning the `.bak` on the
  marker being absent - as `_write_file` originally did - makes the protection
  expire the moment it succeeds, because the first `--force` stamps the marker
  that suppresses every later backup. The rule is therefore: back up whenever
  `--force` would change the bytes on disk, and only then, so an unchanged file
  leaves no litter. Both write paths (`_write_file` for hooks and commands,
  `init_rules_file` for `--client`) share the one implementation specifically so
  the guarantee cannot drift apart again.
- **The semantic cohort is fixed; relevance is never a function of `limit`.**
  `semantic_pool.SEMANTIC_COHORT` / `ENTRANT_CAP` are constants. They used to be
  `limit * 4` and `limit // 2`, which made a memory's semantic rank depend on how
  many rows the caller requested - so `search(q, k)` was not the first k of
  `search(q, K)`, and asking for fewer results returned different, worse
  memories. A rank only means something against a fixed cohort. The constants are
  the geometry at the benchmarked depth, so a limit=10 call is unchanged and the
  recorded benchmark still describes the code. Requests deeper than the cohort
  fetch extra BM25 rows to have enough to return; those rows keep their BM25 rank
  and stay out of the semantic ranking, so a deep call cannot reshuffle a shallow
  one. Ties break on id for the same reason: RRF maps swapped rank pairs to
  identical floats, and the winner must not depend on set iteration order.
- **`sort_by` chooses the retrieval strategy; it is never applied on top of
  one.** A sort layered over a pool that was truncated by a *different* ordering
  reorders a biased sample, not the corpus - so whatever lost the earlier cut
  can never appear, however well it matches the sort. `advanced_search` used to
  fetch `limit * 4` rows by relevance (with a query) or by `last_accessed`
  (without one) and then re-sort them in Python: `sort_by="created"` returned
  the newest of a pool selected on another axis. Measured against a real store,
  `sort_by="created", limit=5` got **0 of 5** rows right and returned memories
  three weeks older than the ones that belonged there. Each ordering is now its
  own strategy in `search_listing.py`, selecting rows in the order it returns
  them: a column sort orders the whole matching corpus in SQL before the limit,
  and a score sort - which SQLite cannot order, since the composite is computed
  in Python - scores every matching row, reading only the six columns it needs
  and fetching bodies for the winners alone. With a query, a date sort runs over
  the FTS match set: a date asks something relevance cannot answer, so the
  semantic cohort, whose membership is itself a relevance judgement, does not
  vote in it. Ties break on `id`, as everywhere else.
- **One column list for `memories`, declared beside the model it fills.**
  `models.MEMORY_COLUMNS` is the single source; `storage`, `context`,
  `search_common` and `portability` all derive their SQL from it via
  `memory_columns_sql()`, and INSERTs generate their `:name` placeholders from
  the same tuple with `memory_placeholders_sql()`. Four private copies used to
  exist and they drifted the moment `pinned` was added: only two gained it. The
  failure mode is what makes this structural rather than tidiness - a short
  column list is still valid SQL and `Memory(**row)` still constructs, so the
  missing field quietly took its default and every search path reported a
  confident `pinned=False` while export dropped the flag. Nothing raised.
  `tests/test_memory_columns.py` holds the tuple against `Memory`'s fields *and*
  against the live schema, so the drift cannot come back silently.
- **The pre-migration backup uses SQLite's backup API, never a file copy.** We
  run WAL, so committed transactions sit in `<db>-wal` until a checkpoint;
  `shutil.copy2` captures the main file and leaves them. That backup is the only
  safety net when a migration goes wrong, so it is the one copy that must be
  current. See `.ai/standards/02-database.md`.
- **The UI ships in the wheel.** `gingugu ui` (`webui.py`) serves the pre-built
  React bundle *and* a live `/api/export` read from one process on one port, so
  pip-installed users get the Memory Explorer with no repo checkout and no Node.
  The bundle is bundled via hatch `force-include` (`ui/dist` → `gingugu/_ui_dist`,
  built in `release.yml` before `uv build`); `webui.find_dist()` falls back to
  `ui/dist` in a source checkout. `--dev` spawns the Vite dev server for hot
  reload. Same-origin serving means the prod path needs no CORS. Static serving
  guards against path traversal and falls back to `index.html` (SPA routing).
- **Never-forget over decay.** Biological-style decay was removed because the
  product promise is "your AI never forgets"; dormancy + spreading activation
  preserves recall quality without deleting history.
- **Hints, not gates.** `similar_memories` / `suggested_relations` point the
  caller at merge and relation candidates but never block a write. They are also
  always compact: an unsolicited extra attached to a write must not cost more
  context than the write itself, so a hint carries a pointer (title + ~200-char
  excerpt) and leaves the body to `memory_recall`.
- **Retrieval finds; an absolute measure adjudicates.** A ranking score answers
  "which of these is nearest", and it cannot answer "is this one actually
  close" - it is normalized against the pool, so its best hit approaches 1.0
  whether or not anything relevant exists. Any surface that reports a number to
  a caller as though it carried magnitude therefore rescores with something
  absolute (`similarity.py`: cosine, or token Jaccard without embeddings) and
  gates on that. The write-time hints shipped in v0.3.8 reporting the fused RRF
  rank score, which made both their thresholds unreachable and fired six
  candidates at every store. Cutoffs are calibrated against a real corpus, and
  carry a `basis` so the caller knows which instrument produced the number.
- **An edge must encode what search cannot infer.** Recall ranks by hybrid
  BM25 + semantic score, so topical adjacency is already free; relations exist
  to record direction and time (`supersedes`, `contradicts`, `caused_by`,
  `parent_of`/`child_of`). `related_to` is therefore a fallback, not a default,
  and guidance is written to optimize edge quality rather than edge count.
  Measured 2026-08-04, when the guidance still said "use liberally": 69% of a
  real brain's 1369 edges were `related_to`, and since `dampened_neighbour_ids`
  ignores `relation_type` those edges were out-competing high-signal ones for a
  per-seed budget of 3.
- **Server resilience over strictness.** Handlers fail soft (structured errors)
  so a bad call never takes down the client's memory layer.

## Future Direction

See `docs/future-architecture.md` — the long-term vision is epistemic governance
(versioned claims backed by evidence) and an embedded cognitive runtime that
wraps model invocation with automatic recall + capture. Roadmap-only, not current work.
