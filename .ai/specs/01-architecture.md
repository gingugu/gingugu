# Architecture

## Overview

Gingugu is a single-process **MCP server**. By default an AI client spawns it
over **stdio**; it can also run over **streamable HTTP** (`gingugu serve`, gated
by a Bearer token) so a hosted/central instance is reachable remotely. It owns
one local SQLite database and exposes a set of memory tools - the entire system
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

1. **Transport** - `server.py` registers MCP tools and routes calls to handlers.
   It is the crash boundary: no exception escapes to the client. Two transports
   share this path: **stdio** (default) and **streamable HTTP** via `serve.py`
   (`gingugu serve`), which wraps the same server in a Starlette app with
   Bearer-token auth middleware and a `/healthz` probe. The `credential_*` tools
   are gated by `MEMORY_CREDENTIALS_ENABLED` so a shared instance can omit the
   secret vault.
2. **Handlers** (`handlers/`) - thin adapters that validate input, call the core
   modules, and return structured dicts. Split by domain: `memory`, `search`,
   `relations`, `admin`, `credentials`, plus `helpers`.
3. **Core** - `storage`, `search`, `embeddings`, `context`, `relations`,
   `consolidation`, `decay`, `stats`, `namespaces`, `portability`. `storage`
   owns the `memories` row only; the satellite tables it drags along have their
   own owners (`tags`, `access`, `embedding_sync`, `claim_sync`), reached
   through the `storage_derived.DerivedTables` delegation surface. They are
   modules over a bare connection rather than methods on `MemoryStore` because
   `portability.import_data` writes memory rows too, and an invariant locked
   inside that class is one the import path cannot honor.
4. **Persistence** - `database.py` owns the SQLite connection and its PRAGMAs
   (WAL, foreign keys, busy timeout). The schema itself lives in the
   `migrations/` package: `schema.py` for structural work (tables, columns,
   FTS5 triggers), `claim_derivation.py` for migrations that only re-read
   existing prose, `runtime.py` for coordination tables that describe the
   processes touching the store rather than the memories in it (`activity`,
   `dream_lock`), and `__init__.py` for the ordered registry and the runner.
   `config.py` resolves the DB path.

## Memory Model

- **Two namespaces layers:** `crow` (global identity/cross-project) + one per
  project. Every memory belongs to exactly one namespace.
- **Typed memories:** `type` ∈ {fact, decision, pattern, bug, architecture,
  preference, workflow, context}; `confidence` ∈ {verified, inferred, stale,
  deprecated}.
- **Graph:** directed typed relations (`supersedes`, `related_to`, `caused_by`,
  `contradicts`, `parent_of`, `child_of`). Recall uses **spreading activation** -
  surfacing a memory wakes its linked cluster.
- **Never-forget:** `decay.py` tracks dormancy (untouched ≥ 90 days) as a
  *resting signal* only. Nothing is auto-demoted or auto-deleted; only explicit
  `memory_forget` removes a memory.
- **Derived time, never stored:** `decay.relative_age()` turns an instant into
  `"2 days ago"` at serialization. Every read surface carries it; nothing
  persists it. Storing a relative timestamp would reintroduce the exact rot
  that `memory_claims` and `review_hints` exist to detect. Precedent:
  `credentials.expiry_status()` and `staleness` classify at call time too - no
  schema column anywhere holds a derived time value.
- **One freshness anchor, and every consumer uses it.**
  `decay.reference_timestamp()` returns the **latest** of `last_confirmed`,
  `updated_at`, `created_at` - a `MAX`, not a `COALESCE`, so an edit made after
  the last confirmation is not discarded. The scorer, the spread-neighbour sort,
  staleness and the payload `age` (`decay.age_label()`) all read it; the two SQL
  call sites mirror the same rule. Feeding it: **a rewrite is a confirmation** -
  `storage.update` advances `last_confirmed` when the title or content actually
  changed, reusing the "did the matching surface move?" test that already gates
  claim re-sync and embedding re-encode. Retypes, tag edits and metadata writes
  assert nothing about truth and leave the clock alone.
- **State claims:** `claims.py` extracts repo-qualified PR/MR references and the
  state a memory asserts about them into `memory_claims`, with
  `claim_qualify.py` answering *which repo* a ref names. That question is
  separable and got its own module once precision work pushed `claims.py` past
  the 300-line limit. Its rules are each pinned to a measured misfire: the
  `#`/`!` sigil is required, because bare "PR 1" in prose names a position in a
  planned series rather than an identity; a ref may not span a line break; a
  repo the prose names is authoritative even when unrecognized, rather than
  being discarded in favour of the namespace default; and a bare ref reuses a
  binding the same memory already stated, so one PR is not counted twice under
  two repos (migration 005;
  migration 006 re-runs the backfill for DBs stranded at v5; migration 007
  re-derives everything under the corrected extractor; migration 009 re-derives
  again for the `unverified` state below).
  The prose is immutable history - a memory that said "PR #10 open" was correct
  when written - so resolution is recorded in the claim row rather than by
  editing the text. This is the primitive whose absence produced 160 distinct
  ad-hoc `=== STATUS ===` banner styles across the dogfooding corpus.

  Refs are qualified by URL, then a repo named beside them, then the
  namespace's `default_repo` - unset means the namespace's own name (the
  one-namespace-per-repo convention), `""` means the namespace is not a repo
  at all. Unqualifiable refs are dropped rather than guessed, and contradiction
  detection is namespace-scoped so a bare-ref mis-key cannot reach across
  namespaces.

  Refs inside `[[wiki-links]]` are ignored: a link to a memory *titled*
  "PR #10 open" is a citation, not this memory's assertion. Namespace scoping
  does **not** contain this one - it produced wrong claims in namespaces whose
  default repo was correct - which is why it is handled in the extractor.

  A ref whose prose asserts no state records as **`unverified`** rather than
  being dropped: dropping made it invisible, and a memory listing `PR #1: <url>`
  under "Deliverables" read as in-flight forever while `claims.open` said 0.
  Calling it open instead was measured against the live corpus and rejected -
  185 such claims against 223 real ones, nearly all narrating shipped work - so
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
  `memory_search(claims="open"|"contradicted"|"unverified")`. One definition, two consumers -
  a stats block and a search filter asking the same question with two
  hand-written correlated subqueries is how they drift apart. The split also
  put `claim_sync.py` back under the 300-line limit.

  `graph_stats.orphan_filter()` follows the same shape for the graph backlog:
  one predicate behind both the `memory_stats.graph.orphans` count and
  `memory_search(orphans=True)`, so a count and its enumeration cannot come to
  disagree about what they are counting. `compute_graph` returns an
  `orphan_sample` alongside the count, since a metric nothing can act on
  describes a cost without offering a way to pay it down.

  The **pinned tier** is the third backlog to get that treatment, and it was
  the last to get it despite being the most consequential. `build_filters`
  gained a tri-state `pinned` (None ignores the flag, True keeps pins, False
  keeps the rest) as a parameter-free WHERE fragment alongside `claim_filter`
  and `orphan_filter`, so it composes with every other filter across all four
  search paths from one addition to the shared `filters` dict.

  `size_stats.compute_size()` supplies the other half. Before it, no surface
  reported bytes at all: `memory_stats` counted rows and `memory_context`
  returned pins mixed into ranked buckets and capped by `limit`, so neither
  "what is pinned" nor "what does it cost" could be asked. Pins are the only
  part of the store paid for on every single call, ahead of and exempt from
  ranking, which makes `pinned_chars` the most consequential number in the
  health payload and made its absence the most expensive one. It lives in its
  own module for the same reason `graph_stats` does - the health overview
  composes independent measurements and each owns its SQL - and that split is
  what kept `stats.py` under the 300-line limit when the block was added.

  **Involuntary recall** is the first retrieval path that is not an MCP tool
  at all. It runs as a Claude Code `UserPromptSubmit` hook, so it answers a
  question no tool can be asked: what should have surfaced without anyone
  requesting it. That inverts the usual precision/recall trade-off, because
  the result is injected rather than returned, and injected context carries
  the authority of the system instead of the tentativeness of a search hit.
  A miss is free; a false positive misleads. Every stage is therefore a
  rejection.

  The split is `recall_gate.py` (pure arithmetic, no I/O, unit-testable
  without a brain) and `recall_sweep.py` (everything that touches the world),
  with `prompt_hook.py` as the entry point. The same seam as `graph_stats` and
  `size_stats`: the decision and its evidence are separable, so separate them.

  The load-bearing idea is the **margin**, not the threshold. An absolute bar
  admitted enough candidates that the 3-memory cap was doing the selecting,
  and a threshold that only ever truncates is not filtering. Comparing each
  hit to the median of its own sweep makes the test relative and self-scaling.
  Pinned and superseded memories are excluded in SQL rather than scored: the
  first already loads unconditionally every session, and the second is
  knowledge the store has itself recorded as replaced.

  `bench/gate.py` exists because the unit tests cannot answer whether the
  thresholds are the right ones - only a real corpus against a real brain can,
  and neither belongs in a public repo. Harness committed, corpus not.

  Edge repair lives in `relation_repair.py`, mixed into `RelationManager`:
  `retype_relation` and `reverse_relation` are the two halves of "the pair is
  right, the label or the arrow is not", and both UPDATE the existing row so an
  edge's provenance survives its correction. Split out when the reversal work
  pushed `relations.py` past the 300-line limit; the same pass moved the batch
  parsing and per-edge dispatch out of `handlers/relations.py` into
  `handlers/relation_ops.py`, leaving the handler owning the MCP surface alone.

## Session identity

`access_log` rows carry the id of the MCP session that requested them
(`access_log.context`), which turns a log of *when* into a log of *alongside
what*. `session.current_session_id` reads the SDK's request `ContextVar`
rather than taking a `Context` handler argument, so no retrieval handler
signature and no published tool schema changes for a bookkeeping field.

The key is the MCP session object, held weakly. That is correct for both
transports with no special-casing: stdio is one session per process, while
`gingugu serve` gives each client its own, and a process-level id there would
manufacture co-access between clients that never shared a conversation. Raw
`id()` was rejected because CPython reuses addresses after collection.

Outside a request the value is `NULL`, never a placeholder: "unknown" is true,
where "these belong together" would not be.

Note the ceiling this inherits. `access_log` is pruned to a rolling 90-day
window, so co-access is a moving picture. Anything built on it has to keep its
own durable aggregate.

## Transactions

Nearly every write here is a single statement that commits itself, which is the
right default: a store call should be durable when it returns. `consolidation`
is the exception - it is `1 + 2N` writes (create, then a `supersedes` edge and a
retirement per original), and a partial application is worse than none. Under
`keep_originals=False` the retirement is a hard delete, so a failure halfway
through the loop destroys memories the surviving record never absorbed.

`transactions.atomic()` takes components that share a connection, closes a gate
on each so its internal `commit()` becomes a no-op, and wraps the block in one
`BEGIN IMMEDIATE`. `MemoryStore` and `RelationManager` participate via the
`TransactionParticipant` mixin. The gate reopens on the way out, so ordinary
single-statement writes are untouched.

Two decisions worth keeping:

- **Embeddings stay outside the transaction.** `embedding_sync` is best-effort
  by design - an encode failure logs and moves on - and that must not become a
  reason to roll back real memories. A vector written inside the block would
  also strand an orphan row if the block later aborts. So `_persist_embedding`
  routes through `_after_commit`, which queues the write while the gate is
  closed and drains it once the data is durable. A failure during that drain is
  logged, never raised: the commit already happened, and reporting a committed
  consolidation as failed would be a lie.
- **`atomic()` does not nest.** A nested call raises rather than silently
  degrading the inner block into a no-op. SQLite would need savepoints for real
  nesting, and nothing here needs them yet.

The same pass split `duplicate_scan.py` out of `consolidation.py` - the
read-only suggest half against the write half - to keep both under the
300-line limit.

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
- **The dream pass computes structure and is forbidden from writing content.**
  A scheduled pass (`dream/`, `gingugu dream`, `memory_dream`) runs PageRank,
  label propagation and cosine similarity over the relation graph and stages
  what it finds in a `proposals` table. The governing constraint is the owner's:
  no AI decides what is in the brain. It resolves cleanly because **math finds
  structure, and structure is not content** - centrality proposes a rank, not
  the conclusion that central means identity; clustering proposes membership,
  not a name; similarity proposes a pair, not a relation type.

  The guarantee is structural rather than careful: nothing in `dream/` has a
  write path to `memories` or `relations`, and `proposals.py` owns its table
  and nothing else. Applying a proposal happens in `handlers/dream.py`, through
  the ordinary managers, only after a person supplies the judgment the pass
  declined to make - and an accept missing that judgment is refused rather than
  defaulted. A default would be the arithmetic choosing after all.

  **A ranking signal has to be independent of when a memory was written.**
  Working the first full queue by hand exposed that both large passes were
  measuring the corpus's *authorship* rather than its meaning: cosine ranked
  session journals above everything because they share a template, and label
  propagation over relations reliably recovered single sessions, since relations
  are laid down between memories saved in one sitting. Clusters therefore rank
  on the members' tags, weighted by inverse document frequency and with
  date-shaped tags dropped - the one property of a group that a burst of saving
  does not manufacture. Coverage alone was measured at barely above chance;
  rarity is what makes it work.

  The corollary is that some findings are unreachable by arithmetic. A group can
  be perfectly cohesive and still be wrong because the name would be false of
  one member - a judgment about meaning, which is exactly the judgment the
  accept step exists to collect.

  Determinism is a requirement, not a nicety. Published label propagation
  randomises node order to sample different local optima; ours fixes the sweep
  order and the tie-break instead, because a pass whose findings change between
  identical runs cannot be audited, and auditability is the entire licence to
  run unattended.

- **The dream pass is scheduled by the OS, not by a daemon we wrote.** Running
  unattended needs a timer, and every platform already ships one with
  restart-on-boot and no supervision to write. What none of them can do is
  decide whether *now* is a good moment. So the recurrence stays outside and
  the judgment moves into the command: `gingugu dream --if-idle` opens the DB,
  reads one row, and exits in 0.42s unless the brain is genuinely unused.

  A long-lived process was rejected explicitly. It buys identical observable
  behaviour and costs a PID file, a restart policy, three platform-specific
  service definitions, and a failure mode where the daemon is dead and nothing
  says so. A program that exits has none of those.

  **The gate reads an `activity` heartbeat, not process liveness**, because a
  running MCP server is not an active user - an editor holds one open for eight
  hours whether or not anyone touches a memory, which is exactly when the pass
  should get its turn. Deriving activity from existing timestamps was rejected:
  reads live in `access_log`, writes in `memories`, edges in `relations`, and
  `idx_access_log_memory_time` leads with `memory_id`, so even the read half
  could not use an index. The heartbeat is installed by wrapping the `tool`
  decorator once in `handlers/__init__.py`, so a tool added later is
  instrumented by the act of registering it, and it is stamped in a `finally`
  because a session spent hitting errors is still a session with a person in it.

  **Unknown activity never reads as idle.** A missing or unparseable heartbeat
  means we have no evidence the user is away, and starting unattended work on
  no evidence is the failure the table exists to prevent.

  **Preemption is bounded by measurement.** A full run is ~24s on a
  1,900-memory brain, so threading cancellation through the passes to reclaim
  at most that would not pay for itself - and the overlap is nearly harmless
  regardless, since WAL readers never block and `busy_timeout` is 30s. The
  check sits *between* passes instead: a pass is the unit that yields a
  coherent set of findings, whatever finished is kept, and every proposal is
  idempotent on re-run, so the next run recomputes exactly what was skipped.
  One threshold serves as both the gate and the cancellation check, so a run
  that would not have started cannot keep running.


- **Local-first, single file.** No server to run, no cloud dependency; the DB is
  portable and inspectable. Trade-off: no built-in multi-user sync (out of scope).
- **Optional network transport, still single-owner.** `gingugu serve` exposes
  the brain over HTTP behind one shared Bearer token for a hosted/central
  instance, but it stays a single SQLite file with no per-user RBAC -
  multi-tenant auth remains roadmap (see `docs/future-architecture.md`).
- **Promotion is a client, not server logic.** `gingugu promote` (`promote.py`)
  speaks the public MCP tool surface to two instances - read-only `memory_export`
  from a local brain, filtered `memory_store` into a central brain with a
  provenance stamp. The server gains no promotion-specific code; the selective
  local→central absorption lives entirely in the client. Keeps the store pure.
- **Onboarding is client-side too.** `gingugu init` (`bootstrap/`) writes no
  server code - it copies packaged templates (`bootstrap/templates/*.tmpl`) into
  a target repo: for Claude Code, a `SessionStart` hook that auto-injects the
  memory startup contract every session (a rules file is not guaranteed to load
  into context; a hook is), a `Stop` save-discipline hook, and the
  `/sink-the-ship` command - merging both hooks into `.claude/settings.json`
  non-destructively (`settings.py`) and appending the hooks' runtime artifacts
  (`logs/`, `.claude/data/`, `settings.local.json`) to the target's `.gitignore`
  so transcripts never get committed. Output is a themed 90s boot sequence
  (`theme.py`, degrades to monochrome off-TTY). Other clients (`--client`) get a
  rules file. This closes the gap where the repo's own hook-based install
  outperformed the copy-paste setup shipped to users.
- **The user-level rules file is part of the bootstrap, and is merged, not
  written.** `bootstrap/global_rules.py` manages the protocol inside a marked
  block in `~/.claude/CLAUDE.md` - the file loaded in *every* session, including
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
  imply the protocol is optional. It exists because that file drifted - it still
  said "build edges aggressively" long after the templates had moved on, with no
  tooling able to correct it. Nothing we ship may name that flag; a test asserts
  the managed note, the module docstring and the run output never do, because the
  note is written into the user's own `CLAUDE.md` and a stale invocation there is
  advice they will follow and watch fail.
- **The same merge now also targets a repo's own `CLAUDE.md` / `AGENTS.md`,
  and `--adopt` is the escape hatch out of the permanent conflict-skip.**
  `merge_block` was already generic over any text, so `init_repo_rules` reuses
  it unchanged - touching only files that already exist in the repo root
  (never creating an `AGENTS.md`), with the same no-`--force` rule as the
  user-level file. But a hand-written protocol hits `conflict` and stays that
  way forever with no flag to opt in - the exact shape a prior finding named
  ("a correct guard can make a feature inert in the field"). `--adopt` breaks
  that: it locates the hand-written section by its own heading TITLE (never
  its body - an early version matched on body text and a nested subsection
  merely *naming* a tool in passing, e.g. "run `memory_recall` before
  asking", outranked the true enclosing heading by having a narrower span),
  wraps that span in the sentinel markers, and immediately re-runs
  `merge_block` to refresh it to the template - one command, one backup of
  the true original.
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
- **A score breakdown is summed from the same arithmetic as the score.**
  `composite_score` is `sum(composite_parts(...).values())` and `score_memory`
  is `sum(score_parts(...).values())`, so what `explain=True` reports and what
  ranked the result cannot be two implementations that drift. The terms are
  weighted contributions, not raw components: they then add up to the number
  they are explaining, and reading them requires no knowledge of the configured
  weights (the raw component divides back out; the ranking consequence does
  not). `memory_context`'s `+0.1` architecture/decision boost is reported as
  its own `type_boost` term for the same reason - folding it into an existing
  term would hide it, and omitting it would leave the terms not summing to the
  score. A result with no ranking behind it carries no breakdown rather than a
  fabricated one: pins never entered the ranking, an `ids` fetch was not ranked,
  and a bare fused relevance has no composite to decompose.
- **Reading inside a memory is deliberately dumb.** `excerpt.py` does literal
  substring matching over character offsets: no ranking, no stemming, no model,
  no embedding. Retrieval is where judgement belongs; once the caller has named
  the memory, "where does this say X" has one correct answer and it should be
  the same answer every time. `total_matches` is reported separately from the
  capped match list so the cap bounds the payload without bounding the truth.
- **One column list for `memories`, declared beside the model it fills.**
  `models.MEMORY_COLUMNS` is the single source; `storage`, `context_buckets`,
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
- **An invariant with no trigger belongs to a module, not to one writer.** FTS5
  keeps itself in step with `memories` because it has triggers; `memory_embeddings`
  has none, so a vector exists only where code deliberately wrote one. That logic
  lived inside `MemoryStore`, which made it unreachable for the other module that
  writes memory rows - `memory_import` - and every restored memory landed
  keyword-searchable but semantically invisible. `embedding_sync.py` now owns the
  invariant and takes `(conn, embedder)`, so any writer can honor it without
  depending on the CRUD layer. The general rule: when a derived table has no
  trigger, the code maintaining it must be reachable by every writer of the
  source table, or the invariant is held by whoever remembered.
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
  then ignored `relation_type` those edges were out-competing high-signal ones
  for a per-seed budget of 3.
- **Retrieval prefers directional edges, but confidence outranks type.**
  (2026-08-27.) `dampened_neighbour_ids` sorts neighbours by confidence rank,
  then `models.RELATION_WEIGHT`, then low degree, then recency, then id.
  The weight table is deliberately **two tiers** - 1 for every directional
  type, 0 for `related_to` - because nothing measured ranks `supersedes` above
  `caused_by`, and a finer order would encode a guess as a ranking rule.
  Confidence stays *above* type for a concrete reason: `supersedes` habitually
  points at the deprecated memory it replaced, so ranking type first would make
  every such edge a channel for surfacing what the graph records as no longer
  true. `graph_stats.HIGH_SIGNAL_TYPES` derives from the same table so the
  health stat and the ranking cannot drift apart.
  Measured on the real brain (only the sort differing): the share of the spread
  budget reached by a directional edge went 67.7% → 73.0%, with the neighbour
  count flat at the `SPREAD_TOTAL` cap and every retrieval metric unchanged.
  Context for the size of that move - the brain is 66.3% directional by edge
  count, so the old traversal was merely mirroring the edge mix and expressing
  no preference at all.
- **A neighbour is a memory, not an edge.** Two memories may be joined by
  several edges (different types, or one row in each direction); the pair is
  scored by its strongest. Before 2026-08-27 the traversal grouped per edge, so
  such a pair spent two of a seed's three slots and appeared twice in the
  payload.
- **Server resilience over strictness.** Handlers fail soft (structured errors)
  so a bad call never takes down the client's memory layer.

## Future Direction

See `docs/future-architecture.md` - the long-term vision is epistemic governance
(versioned claims backed by evidence) and an embedded cognitive runtime that
wraps model invocation with automatic recall + capture. Roadmap-only, not current work.
