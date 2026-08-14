# Changelog

All notable changes to Gingugu will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **The `unverified` claim state** — a memory naming a PR or MR whose prose
  never said what became of it produced *no claim at all*. `PR #1:` followed by
  its own URL under a "Deliverables" list asserted nothing the extractor could
  see, so `claims.open` read 0 while the memory read as in-flight to a human
  forever. The backlog was not merely unenumerable, as it was before `#47`; it
  was undetected.

  The obvious fix — treat a state-less ref as open — was measured against a
  real 1161-memory corpus and rejected. 225 ref mentions (185 distinct claims)
  are named with no asserted state, against 223 real claims, and they
  overwhelmingly narrate work that already shipped: *"Fixed in PR #873"*,
  *"PR #121 deployed successfully"*. Emitting those as open would more than
  double the backlog with history — the exact failure the extractor already
  refuses elsewhere, where a missed claim is silent and a wrong one teaches the
  reader to ignore claims entirely.

  Such a ref now records as `unverified`, asserting only that the memory names
  it and never says what became of it. It is excluded from `claims.open`,
  `open_actionable`, `claims.sample`, and contradiction detection, and is read
  through `memory_search(claims="unverified")` — a browsable index, not a queue.
  `claims.unverified` reports the count alongside.

  `resolve_claims="all"` deliberately still means every *open* claim: closing an
  unverified ref under "all" would record that the caller checked something they
  never looked at. Naming the ref explicitly resolves it, which is the honest
  way to say "I looked, and it merged".

  Migration 009 re-derives existing stores through `claim_rederive`, preserving
  every recorded resolution. No schema change: `state` was already unconstrained
  TEXT. Where migration 007 only ever removed claims, this one only ever adds
  them.

- **Orphan enumeration** — `memory_stats`' `graph` block reported that N
  memories had no relation touching them and nothing could name one of them. An
  orphan is reachable only by direct search, since spreading activation can
  never wake it, so the count described a real retrieval cost with no way to
  work through it; reconnecting one meant querying the database behind the
  server's back. Same shape as the claims backlog before it became enumerable.

  `graph.orphan_sample` now names them, ordered by confidence, then access
  count, then recency, so the orphans costing the most retrieval come first,
  each row carrying its namespace. The existing `review_limit` raises the cap
  (max 100), making one knob cover the review sweep, the claims backlog and the
  graph backlog alike. Deprecated orphans sink to the bottom of the sample
  rather than being filtered out, so the list is drawn from exactly the
  population the count reports and no second number is needed to explain a gap.

  `memory_search(orphans=True)` pulls the same set with full bodies, composing
  with every other filter, with or without a query. Both are backed by one
  `graph_stats.orphan_filter()` predicate: a count and its enumeration must be
  counting the same thing, and two hand-written copies of the same subquery is
  how they stop.

- **`memory_unrelate(reverse=True)`** — turn a backwards edge around. Retyping
  covered "right connection, wrong label"; this covers "right pair, wrong
  direction", which the graph's own preference for directional types makes an
  easy error to write. `A caused_by B` recorded for `B caused_by A` is a false
  claim about causality, not an untidy one.

  The endpoints are swapped on the existing row, so id, `created_at` and
  metadata survive exactly as they do for a retype. It combines with
  `new_relation_type` in a single write, because an edge recorded backwards is
  frequently mislabelled as well; reversing `parent_of`/`child_of` is the same
  operation as flipping between them, so do one or the other. Available singly
  and in a batch (`edges[].reverse`), with `dry_run` reporting `would_reverse`
  and an existing edge in the target direction absorbing this one as `merged`.

  Without it, straightening an edge cost a delete plus a `memory_relate` — two
  calls that discard the provenance the repair path exists to protect.

### Changed

- `relations.py` and `handlers/relations.py` both crossed the 300-line limit as
  a result, so each gave up a half along its natural seam: repair operations
  (retype, reverse, delete) moved to `relation_repair.py`, mixed into
  `RelationManager`, and batch parsing plus per-edge dispatch moved to
  `handlers/relation_ops.py`, leaving the handler owning the MCP surface. Public
  API unchanged in both cases; no tool-surface change.

---

## [0.16.0] - 2026-08-13

### Added

- **`memory_unrelate`** — edge repair. Retype a mislabelled relation in place,
  or remove one that should not exist. Retyping is an in-place UPDATE, so the
  edge keeps its id, `created_at` and metadata: the usual repair is "right
  connection, wrong label", and when the link was drawn stays true. Retyping
  onto a type that already joins the pair collapses the two and reports
  `merged` rather than `retyped`, because the edge count genuinely drops by
  one. Deleting without a `relation_type` removes every edge between the pair;
  memories are never touched.

  Batches of up to 100 per-edge operations submit in one call, validated whole
  before anything is written so a malformed op cannot leave the graph
  half-repaired. `dry_run` previews a sweep. There is deliberately no
  criteria-driven form: retyping exists because each edge deserves a different
  type based on what it records, and a blanket relabel would manufacture
  directional claims that were never true.

  The relation surface was create-only, so an edge written in haste was
  permanent for the life of both memories — and since spreading activation
  visits at most 3 neighbours per seed without weighting by type, every wrong
  edge kept competing for a slot against a right one. Precision was demanded,
  errors were unfixable, and the cost was paid on every future recall.

- **`memory_edges`** — read-only edge enumeration, the discovery half of the
  same job. `memory_stats.graph` could report that a graph was 70%
  `related_to`; nothing could say which edges those were, so repair meant
  querying SQLite by hand. Rows carry both endpoints' ids, titles and
  namespaces, the relation type, `created_at`, and each endpoint's degree —
  degree being what decides whether an edge can ever fire. Filters by
  namespace (either endpoint), relation type, or a single memory; paged with a
  stable order.

- **`memory_search(claims="open"|"contradicted")`** — the state-claim backlog as
  a first-class corpus. Returns full bodies of memories still asserting a PR/MR
  is open, or the subset a later memory in the same namespace already recorded
  as resolved. Composes with `query`, `type`, `namespace`, `tags`, and
  `sort_by`, so `claims="open", namespace="gingugu", sort_by="created"` is a
  working sweep in one call.

- **`memory_stats.claims.open_actionable`** — open claims excluding those on
  deprecated memories, which is exactly the set `claims.sample` lists. `open`
  still counts every unresolved claim; reporting only that number left a caller
  comparing it against `len(sample)` with no explanation for the gap.

### Changed

- **`memory_stats.claims.sample` now enumerates the whole backlog** instead of
  only its contradicted subset, ordered contradicted-first, each row tagged
  `contradicted` so the priority survives. `review_limit` raises the cap to 100
  as before.

  The original scoping was defensible — a contradiction is answerable
  immediately from what the brain already holds — and it made the block
  unusable for the one job it exists for: a namespace could report five open
  claims and offer no way to learn which five. A count without an enumeration
  is a dead end wearing a metric's clothes.

  Contradiction detection remains scoped **within** a namespace. Bare refs are
  keyed off the namespace's default repo, so matching across namespaces would
  pair two different repos' `PR #12`; a missed contradiction is silent, a
  fabricated one teaches the reader to ignore the metric.

### Removed

- **The `How It Compares` section is gone from the README**, along with its
  table-of-contents entry. The capability matrix it once held was retired in
  favour of prose in a 2026-07-07 docs pass; the prose is now retired too.
  Gingugu's docs
  describe what Gingugu is and does, in absolute terms - what one SQLite file,
  MCP-native transport, typed relations, and no-LLM-call writes buy you. They
  do not rank the project against other people's roadmaps. The same section
  came out of gingugu.com in the same pass.

### Internal

- Claim reads split from writes: the backlog query and the shared
  `claim_filter()` predicate now live in `claim_queries.py`, used by both the
  stats block and the search filter so one correlated subquery has one
  definition. Returns `claim_sync.py` to the 300-line limit.

---

## [0.15.0] - 2026-08-13

### Added

- **Pinned memories: a tier that always loads, exempt from ranking.**
  `memory_update(pinned=True)` marks a memory as unconditionally loaded by
  `memory_context` for its namespace.

  Ranking answers "what is most relevant to this task?" It cannot answer "what
  must never be missing?" Those are different questions, and every governing
  rule was competing for a context slot against topical trivia on the same
  axis — a hard-won "never do X" and a piece of packaging arcana ranked
  identically, and which one surfaced came down to the task hint.

  Pins are **additive to `limit`**, not a share of it. A tier that truncates
  under contention recreates the exact failure it exists to fix, so the blast
  radius is bounded by a per-namespace cap (20) instead: the write path refuses
  a new pin past it and tells you to unpin rather than raising it. Re-pinning
  an existing pin stays idempotent at the cap. Deprecation beats a pin — "no
  longer true" outranks "never let me miss this" — and pinning never advances
  `last_confirmed`, so it cannot silently suppress the review hints on the
  memories where staleness would hurt most.

  Schema v8 (`memories.pinned`, partial index). Existing stores gain the column
  and change no behaviour until something is explicitly pinned.

- **Relation-graph health in `memory_stats`.** A new `graph` block reports edge
  count, edges per memory, the breakdown by relation type, orphan count and
  ratio, and how many memories carry more edges than spreading activation will
  ever visit.

  The knowledge graph is the part plain search cannot replace, and nothing
  measured it — so the signals that predict retrieval failure were invisible:
  orphans reachable only by direct search, a graph dominated by the
  `related_to` fallback that encodes little the text index doesn't already
  infer, and edges stranded past the `SPREAD_PER_SEED` cap where they can never
  fire. Read-only aggregates; no schema change.

### Changed

- `memory_context` excludes this namespace's pinned memories from the ranked
  buckets in SQL rather than after the fact, so pins can't quietly consume the
  discovery slots they were already guaranteed.
- Tests assert against a derived `LATEST_SCHEMA_VERSION` instead of a hardcoded
  number, so a migration no longer breaks twenty unrelated assertions.

### Documentation

- README leads with the shipped session protocol (`gingugu init`'s SessionStart
  and save-discipline hooks), publishes the measured retrieval numbers (MRR
  0.828, recall@1 0.611, recall@5 0.983 over the in-repo `bench/` golden set),
  and adds an **Upgrading** section covering the package upgrade, the client
  restart, `gingugu init --force` to refresh repo hooks, and how to diagnose an
  upgrade that appears not to take.

### Tests

- End-to-end dormancy lifecycle coverage: a memory crossing
  `DORMANT_AFTER_DAYS` is counted dormant, an ordinary `memory_recall` wakes a
  dormant neighbour through its relation without inflating `access_count`, and
  the accounting flips back. The 90-day threshold means this path could not
  have run in production yet; it is now verified rather than assumed. Also
  pins the deliberate behaviour that a `memory_context` load refreshes the
  dormancy clock, so routinely surfaced memories never go dormant.

---

## [0.14.0] - 2026-08-13

### Added

- **`gingugu init` now manages the memory protocol in your user-level
  `~/.claude/CLAUDE.md`.** Previously `init` only ever wrote inside a target
  repo, so the one rules file loaded in _every_ session — including sessions
  started in a directory with no project protocol installed — was hand-
  maintained with no tooling behind it. It drifted exactly as you'd expect: it
  was still telling agents to "build edges aggressively" long after the shipped
  templates had moved on.

  The protocol now lives in a marked block that `init` owns. It is strictly
  additive outside those markers:

  - missing file → created
  - existing content, no markers → block **appended below it**, every prior byte
    preserved
  - managed block present → replaced **in place**; your prose before _and_ after
    it survives, and an unchanged result is a no-op
  - a memory protocol already there that `init` doesn't manage → **nothing is
    written**; it warns and explains how to opt in. **No flag overrides this** —
    `--force` authorizes overwriting the repo files `init` owns, and must not
    also authorize appending a second set of rules to a hand-authored file
  - a `.bak` is written only on the refresh path, since appending risks nothing

  `init` now also prints the **resolved target directory** as its first line.
  `--path` defaults to the process's cwd and wrappers move that out from under
  you — `uv run --directory X gingugu init` runs in `X`, so it bootstraps `X`
  rather than the directory you typed the command in. Naming the path up front
  turns a silent wrong-repo write into something you catch immediately.

  Re-running `gingugu init` after an upgrade is how you pick up protocol changes.
  There is no `--global` flag: this is part of the Claude Code bootstrap in the
  same way the hooks and the non-destructive `settings.json` merge are. Other
  `--client` targets never touch it.

### Fixed

- **`age` no longer makes a maintained memory look stale.** The field shipped in
  v0.13.0 for one job — at session start, under the `compact` mode the protocol
  mandates, let the agent tell last night's RESUME note from June's. It was
  derived from `created_at`, the one timestamp that never moves, so it was at
  its most misleading for exactly the memories that get **maintained**: RESUME
  notes, running lessons with several sightings, anything rewritten in place. A
  memory stating today's truth could read as 7 weeks stale.

  Three nested defects, fixed together:

  - **`age` is anchored on the freshness anchor**, the same instant the scorer,
    the spread-neighbour sort and staleness already use — so the payload no
    longer disagrees with the ranking. Where the anchor differs from
    `created_at`, `age` reports both halves: `"7 weeks ago (updated just now)"`.
    That parenthetical costs ~4 tokens and appears only on maintained memories,
    which is where the distinction carries information — "durable AND current"
    is a stronger signal than either half.
  - **The freshness anchor is a `MAX`, not a `COALESCE`.** It was documented as
    a null-safe fallback, but `COALESCE` returns the first non-null: a content
    edit made *after* the last confirmation was discarded outright, and the
    memory was scored, spread-sorted and staleness-checked off the older
    instant. Fixed in Python and in the two SQL call sites.
  - **A rewrite is a confirmation.** `memory_update` now advances
    `last_confirmed` when the title or content actually changed — someone
    re-read the claim and restated it. Previously the clock only moved when a
    caller explicitly passed `confidence="verified"`, so ordinary content
    maintenance never registered and the freshness signal silently rotted.
    Retypes, tag edits and metadata writes assert nothing about truth and still
    leave it alone. Trade-off worth knowing: a one-word typo fix also resets the
    staleness clock, suppressing `review_hints` and `suggests_deprecation` for
    that memory.

  Retrieval quality is unchanged: benchmarked A/B against a real 909-memory
  brain, all metrics and every per-question score identical.

- **`gingugu init --force` no longer destroys a customized hook without a
  backup.** The "is this file ours?" signature was the bare word `gingugu` —
  which every gingugu-aware hook contains, because the MCP tool names are
  `mcp__gingugu__*`. A heavily customized local `stop.py` was therefore
  classified as ours and overwritten with **no `.bak`**; only a clean git tree
  saved it. Every shipped file now carries a distinctive
  `gingugu-init:managed-file` marker, and anything lacking it is backed up first.

- **The foreign-flag warning no longer cries wolf.** It compared a wired hook
  command's flags against a hardcoded list of _our template's_ flags, so a repo
  running its own richer same-named hook — one that genuinely accepts `--chat`
  and `--notify` — was reported as "written for a different script" when the
  wiring was correct. It now reads the `add_argument` declarations from the
  script actually installed on disk, so it stays quiet when the flags are
  accepted and still fires when they are genuinely orphaned (which is the real
  hazard: `parse_known_args` means orphaned flags are silently ignored at
  runtime rather than erroring).

### Changed

- **The shipped memory-protocol template covers more of the tool surface.**
  Added the credential vault (`credential_list` first, before ever asking the
  user for a secret), `memory_forget` for wrong information, namespace creation
  when a repo has none, and a concrete list of save triggers rather than a bare
  instruction to save often.

- **Relation guidance now optimizes edge _quality_, not edge count.** Every
  guidance surface that drives relation-writing was reversed: the
  `memory_relate` tool description, the `suggested_relations` framing on
  `memory_store` / `memory_update`, and the three `gingugu init` templates
  (`rules_protocol`, `sink-the-ship`, `stop`). Directional types
  (`supersedes`, `contradicts`, `caused_by`, `parent_of`/`child_of`) are now
  ranked first and `related_to` is explicitly a fallback, never shorthand for
  "similar topic".

  Measured on a real 909-memory brain: **69% of 1369 edges were `related_to`**.
  That type encodes topical adjacency, which hybrid BM25 + semantic search
  already derives at read time — so those edges duplicated the index while
  costing a tool round trip each to write. Worse, `dampened_neighbour_ids`
  sorts by confidence, degree, and recency but **never reads
  `relation_type`**, so with a budget of 3 neighbours per seed the low-signal
  majority was out-competing the 31% of edges that carried real signal.

  The old wording caused it directly: `AGENTS.md` described `related_to` as
  "most common — use liberally" and framed the goal as building edges
  "aggressively", with a rule of thumb measured in edge _count_. No storage,
  schema, scoring, or response shape changed — this is guidance and framing
  only, plus tests that fail if the volume-first vocabulary returns.

---

## [0.13.0] - 2026-08-04

### Added

- **`age` on every memory payload.** Reads now carry a derived, human-readable
  interval — `"age": "2 days ago"` — alongside the existing fields. It ships in
  full reads, `compact` reads, and write-time hints.

  `_compact_summary` drops timestamps by design, and the session protocol
  mandates `compact=true` at session start. So at the one moment temporal
  context matters most — reading the RESUME memory — the agent could not tell
  last night's note from June's. Raw ISO timestamps already ship in full mode
  and still get misread, because the date arithmetic is done unreliably or
  skipped outright; deriving the interval removes the arithmetic. ~4 tokens
  per memory.

  **The value is never persisted.** `decay.relative_age()` computes it at
  serialization time from `created_at`, the same lifecycle as `score` and
  `credentials.expiry_status`. A stored `"6 days ago"` would be wrong the
  moment the world moved on — exactly the bug class `memory_claims` and
  `review_hints` exist to catch.

### Fixed

- **The startup contract no longer asks the agent to infer the workspace.**
  `gingugu init`'s `session_start.py` told the agent to "Append any other
  workspace repos to the list". A SessionStart hook receives exactly one
  directory — `cwd`. There is no workspace roster in the payload, so the
  contract was asking for an inference it had supplied no data for.

  The agent reached for the only workspace-shaped list available to it,
  Claude Code's "Additional working directories" — a *permission allowlist*,
  not a workspace — and loaded five namespaces at startup instead of two.

  The contract now states a floor and a rule instead of inviting a guess:
  `crow` plus the `cwd` repo always, and any other namespace only when the work
  actually reaches that repo. Multi-repo work is unaffected — namespaces load
  on demand rather than speculatively. **Existing installs need
  `gingugu init --force` to pick this up.**

---

## [0.12.0] - 2026-08-01

### Changed

- **Write-time hints are compact.** `memory_store`'s `similar_memories` and
  `suggested_relations` (and `memory_update`'s `suggested_relations`) returned
  each candidate's **full body**. A single store could attach six complete
  memories to the response — measured against the live 821-memory corpus, a
  median-sized candidate set cost ~11,300 characters (~2,800 tokens) of the
  caller's context, on every write, unasked for and often larger than the
  memory being saved.

  They now use the same compact shape as `compact` reads: title plus a
  ~200-char excerpt under `summary`, no bookkeeping fields. ~89% smaller. A
  hint is a pointer, not a payload — it carries enough to decide whether to
  merge, link, or move on, and `memory_recall` is one call away when a
  candidate warrants a closer look.

  The memory the caller just wrote is **unchanged** and still returns in full;
  only the unsolicited extras were trimmed.

---

## [0.11.1] - 2026-07-30

### Fixed

- **The `Stop` hook no longer crashes on flags it doesn't own.** It called
  `parse_args()`, so any unrecognized flag made argparse `sys.exit(2)`. That
  raises `SystemExit`, a `BaseException` — the script's own `except Exception`
  could not catch it — and Claude Code reads the non-zero exit as a blocked
  stop. A repo whose `settings.json` was written by other tooling (which
  routinely appends its own flags to the `Stop` hook) had every session break.

  Now `parse_known_args()`. Flags we don't own are ignored, which makes the
  failure *impossible* rather than merely detected, and un-breaks affected
  repos with no `settings.json` edits at all.

- **`gingugu init` no longer reports incompatible wiring as "already wired".**
  It detected an existing `Stop` hook by looking for the bare filename
  `stop.py`, so a command pointing at a *different* tool's same-named script
  counted as correctly configured. It now inspects the flags in that command
  and warns when they are ones our script does not accept, instead of claiming
  success.

- **`gingugu init --force` no longer silently clobbers a hook it didn't
  write.** A `stop.py` with no gingugu signature is backed up to `stop.py.bak`
  and the overwrite is reported with a warning pointing at the `settings.json`
  command that may also need updating.

- **`default_repo` now actually takes effect.** Setting it changed the column
  and nothing else: claims are stored rows and the default repo is only read
  at extraction time, so every already-derived ref kept its old key. There was
  no supported way to apply the declaration — no MCP tool exposes a re-derive,
  and `storage.update` only re-syncs claims when the prose actually changed,
  so the only remaining route was editing memory text, which is precisely the
  dodge the claims design exists to make unnecessary.

  `memory_namespaces(action="update", …, default_repo=…)` now re-derives that
  namespace's claims when the value changes, preserving resolution state.
  Shipped inert in 0.11.0; the upgrade note in that release did not work.

---

## [0.11.0] - 2026-07-30

### Fixed

- **Refs inside `[[wiki-links]]` no longer create claims.** A link to a memory
  titled `PR #10 open: the promotion bridge` is a *citation*, not this
  memory's assertion about PR #10 — but the extractor read the two the same
  way. Because titles are exactly where "PR #N open" phrasing lives, every
  memory linking to a claim-bearing memory inherited its claim, and the effect
  compounded as the graph got more linked.

  Measured on a 785-memory corpus: 11 wrong claims, **8 of them in a namespace
  whose default repo was perfectly correct** — so the existing
  namespace-containment guarantee never covered this. In the worst case a
  memory titled `RESOLVED: internal gateway crashloop` was asserting
  `#155 open`, purely because of what it linked to.

  Nothing is lost by dropping them: when a claim's only state evidence sits
  inside a link, the *linked* memory already holds that claim, correctly keyed.

### Added

- **`memory_namespaces` gains `default_repo`**, controlling what a bare
  "PR #12" means in a namespace:

  | `default_repo` | Behavior |
  | --- | --- |
  | unset (default) | Falls back to the namespace's own name — the one-namespace-per-repo convention. |
  | a repo slug | Uses that slug. For namespaces named differently from their repo. |
  | `""` | The namespace is **not** a repo. Bare refs are dropped instead of mis-keyed. |

  Previously every namespace was assumed to be a repo, so a bare ref in an
  identity or notes namespace keyed to a repo that cannot exist (`crow#32`).
  Measured: 20 such claims, all inert — contradiction detection is
  namespace-scoped, so they could only ever collide with each other.

  The unset default is deliberate and load-bearing: measured over 764
  memories it is the difference between 145 claims and 26.

### Changed

- **Migration 007** adds `namespaces.default_repo`, seeds `crow` and `default`
  as non-repo namespaces, and re-derives every claim under the corrected
  extractor. On the reference corpus this pruned 158 claims to 130 in ~200ms
  and added none.

  Resolution state survives. The re-derive deliberately does **not** go
  through `claim_sync.sync_claims`, which drops `resolved_*` by design because
  it runs when a memory's *prose* changed. Here the prose is untouched and only
  the extractor improved, so discarding resolutions would destroy manual
  reconciliation work that cannot be recovered.

  If you keep memories in a namespace that is not a repo, declare it after
  upgrading: `memory_namespaces(action="update", name="notes", default_repo="")`.
  A user who genuinely has a repo named `crow` restores it the same way.

---

## [0.10.1] - 2026-07-30

### Fixed

- **Databases upgraded to schema v5 by a pre-release build have their state
  claims backfilled by a new migration 006.** Migration 005 shipped in two
  forms: an early one that created `memory_claims` empty, and the released one
  that also backfills it. Migrations are selected with `current < target`, so
  any database already stamped v5 by the earlier form could never run 005
  again — it was left with an empty claims table permanently, and no reinstall
  or restart could fix it. Migration 006 re-runs the backfill. It adds no
  schema.

  This only affects databases upgraded from a pre-release build; every
  0.10.0 install from PyPI backfilled correctly on its v4 → v5 upgrade.

  The repair is idempotent (`INSERT OR IGNORE` against
  `UNIQUE (memory_id, kind, ref)`), so it is a few hundred milliseconds of
  no-ops on an already-populated database and leaves existing resolution state
  untouched. Claims are re-derived from each memory's current text, so a
  reference edited out of a memory is not resurrected.

---

## [0.10.0] - 2026-07-30

### Added

- **State claims: a memory's PR/MR references are now tracked as data, so a
  claim can go stale without its prose being edited.** A memory that said
  "PR #10 open" was correct when written; the text is history and stays put.
  New `memory_claims` table (schema v5) records what each memory *asserts*,
  with resolution stored in separate columns beside it.
  - `memory_store` / `memory_update` return **`contradicted_memories`** when a
    write resolves a ref that another memory in the same namespace still calls
    open. That is the cheapest moment to reconcile — the caller is already
    thinking about that exact PR. The key is omitted, not empty, when there is
    nothing to report.
  - `memory_stats` gains a **`claims`** block: `open`, `resolved`, and
    `contradicted` counts plus a `review_limit`-capped sample.
  - `memory_update` gains **`resolve_claims`** (comma-separated refs, or
    `"all"`), which records a resolution and leaves the memory body
    byte-identical. Use `content` only when a memory asserts something that was
    never true.
  - Refs are repo-qualified (`gingugu#10`) by URL, then by a repo named beside
    them, then by the namespace name. Unqualifiable refs are dropped rather
    than guessed, and contradiction detection is namespace-scoped.
  - No new MCP tool: the loop is `memory_stats` → `memory_search(ids=…)` →
    `memory_update(resolve_claims=…)`, reusing the existing sweep.
  - **Upgrading populates claims for memories you already have.** Migration 005
    backfills from existing text (~210ms for 735 memories) rather than creating
    an empty table, so the feature works on first restart instead of waiting
    until you happen to edit each memory.

- **`memory_update` accepts `type`.** A misfiled memory can now be retyped
  through the MCP surface. Previously the only fields exposed were title,
  content, confidence, metadata and tags, so the standard remedy for a
  wrongly-typed memory (retype it to `pattern`/`preference`, which are exempt
  from gated review hints) was impossible to perform. Retyping does not
  re-embed: the vector derives from title + content only.

### Fixed

- **Review hints no longer fire on prose that merely contains the trigger
  words.** Three changes, measured against a 751-memory corpus:
  - `waiting-on` now requires the wait to name an agent (a person, a PR/MR, a
    ticket key, or a named artifact). "blocks forever waiting for EOF" and
    "waiting for first init container image pull" describe a mechanism, not a
    status, and flagged permanently.
  - No signal fires from inside quotes or backticks. A memory *citing*
    `"expire 2026-06-29"` is describing the phrase, not claiming the state.
    Only `"` and `` ` `` delimit — a bare `'` is far more often a possessive.
  - `expired-date` and `stale-as-of-date` are suppressed when the memory was
    explicitly reconfirmed *after* the date it names. The outcome is already
    recorded in the body; re-flagging it forever asks for work already done.
- **Deprecated memories no longer carry review hints on read surfaces.**
  `memory_stats` has always excluded them, but `memory_recall`/`memory_search`
  /`memory_context` did not, so the surfaces disagreed: a memory `memory_stats`
  refused to count could still arrive stamped with a hint. A deprecation *is*
  the reconciliation.

---

## [0.9.1] - 2026-07-29

### Fixed

- **`gingugu ui` now includes the bundled UI when installed from PyPI.** The
  0.9.0 wheel was built from the sdist, which does not carry the (gitignored)
  built `ui/dist`, so the Memory Explorer assets were missing. The release now
  builds the wheel directly from source so `gingugu/_ui_dist` is bundled.

---

## [0.9.0] - 2026-07-21

### Added

- **`gingugu ui` command.** Launch the Memory Explorer web UI with one command.
  In the default (prod) mode a single process serves the pre-built React bundle
  plus a live `/api/export` read of your database on one port
  (http://127.0.0.1:5174) and opens your browser - no Node.js required, because
  the built UI now ships inside the wheel. Flags: `--host`, `--port`,
  `--no-browser`. `gingugu ui --dev` runs the API backend and the Vite dev
  server together for hot-reload UI development (repo checkout + Node required),
  replacing the old two-terminal workflow.

### Changed

- The Memory Explorer serving logic moved into the package (`gingugu.webui`) so
  it can ship in the wheel and back `gingugu ui`. `ui/api.py` remains as a thin
  shim for the `uv run python ui/api.py` dev workflow. The release build now
  compiles the UI (`npm run build`) before packaging so the wheel carries it.

---

## [0.8.1] - 2026-07-20

### Fixed

- **CLI front door.** `gingugu` now handles `-h`/`--help`/`help` (usage) and
  `-V`/`--version`/`version` (version) instead of silently falling through to
  the stdio server. An unknown subcommand prints an error plus usage to stderr
  and exits `2` rather than blocking on stdin. Bare `gingugu` still runs the
  MCP stdio server, and `serve`/`promote`/`init` keep their own `--help`.

---

## [0.8.0] - 2026-07-20

### Added

- **Fetch memories by ID.** `memory_search` accepts `ids` (comma-separated) —
  the precise-fetch path for IDs handed out elsewhere (e.g. a `memory_stats`
  review sample). Results return in the requested order, deprecated memories
  included, with a `missing` list for any ID not found.
- **Enumerable review sweeps.** `memory_stats` accepts `review_limit`
  (default 5, max 100) to raise the `review.sample` cap, so a reconciliation
  sweep can list every flagged memory instead of the top 5 — pair with
  `memory_search(ids=…)` to pull the full bodies.

### Fixed

- **Review-hint false positives on timeless types.** Gated review signals
  (`waiting-on`, `open-pr-reference`, `unmerged-branch`) no longer fire on
  `pattern`/`preference` memories, whose prose is reference material — "apps
  blocked on disk I/O" in a diagnostic pattern is not an in-flight status
  note. Ungated date signals still apply to every type.

---

## [0.7.0] - 2026-07-18

### Changed

- **Hub-dampened relation traversal.** `include_related` extras and
  spreading activation now share one budgeted neighbourhood
  (`RelationManager.dampened_neighbour_ids`): each seed contributes its 3
  most trusted, most specific neighbours (confidence rank, then low
  relation degree, then recency — fully deterministic), capped at 10
  total, so one highly-connected "hub" memory can't drag its whole
  cluster into every recall or reset its neighbourhood's dormancy clocks.
  Measured on a real brain (30-question golden set, 10 seeds each):
  mean extras 18.9 → 9.9, mean extra payload ~9.4k → ~4.8k tokens, worst
  case 29 → 10. Seed retrieval is untouched.

- **True hybrid retrieval.** `memory_recall` / `memory_search` now pull the
  BM25 (FTS5) and semantic (cosine-over-embeddings) candidate pools
  **independently** and fuse them with Reciprocal Rank Fusion over their
  union — a memory that matches the query's meaning surfaces even when it
  shares no keywords with it. BM25 candidates always keep their semantic
  rank; semantic-only entrants join above a 0.55 similarity floor (at most
  `limit/2` of them), so weak lookalikes can't crowd out keyword matches.
  Benchmarked on a real brain (30 labeled questions): MRR 0.811 → 0.828,
  recall@1 0.578 → 0.611, recall@10 1.000 held. `search.py` split into
  `search.py` (hybrid engine), `search_common.py` (shared SQL fragments),
  and `search_filters.py` (`advanced_search` + metadata listing).

### Added

- **Retrieval benchmark toolset (`bench/`, dev-only — not shipped in the
  package).** Golden-set benchmark measuring recall quality with deterministic
  metrics (Recall@K, MRR, precision@K, context-token cost) — no LLM-as-judge,
  ever. Two tiers: a committed synthetic fixture runs as a CI regression
  floor (`uv run python -m bench`), and `--db` mode scores a real brain
  (opened strictly read-only) against gitignored golden sets under
  `bench/local/`. The runner mirrors the live `memory_recall` path but never
  mutates ranking signals. Ranking/scoring changes are validated against a
  recorded baseline.

---

## [0.6.0] - 2026-07-08

### Added

- **Multi-namespace `memory_recall` and `memory_search`.** The `namespace`
  parameter now accepts a comma-separated list (e.g. `"crow,my-project"`),
  searched in one ranked pass at the SQL layer. Unlike `memory_context`
  (per-namespace limit), `limit` caps the **total** merged result list. A
  multi-namespace response carries `namespaces`; single-namespace responses
  keep their historical shape. Every memory returned by recall/search is now
  stamped with its home `namespace` name, matching `memory_context`. Closes
  the observed failure where an agent generalized the CSV form from
  `memory_context` and got `namespace 'a,b' not found`.
- **`compact` mode on `memory_recall` and `memory_search`.** Same payload diet
  `memory_context` got in 0.4.0: title + a ~200-char `summary` excerpt instead
  of full content, bookkeeping fields dropped, `include_related` extras
  compacted too. Fixes broad recalls blowing past MCP clients' tool-result
  token caps (Claude Code dumps oversized results to a file the agent must
  chunk-read back). Compact recalls still credit access — only the payload
  changes, not the semantics. Flow: compact sweep to see the landscape, then a
  targeted follow-up for the memory that matters. Compact summaries now also
  carry `namespace_id` (identity, not bookkeeping) so namespace stamping works
  uniformly across all read surfaces.
- **Comma-aware namespace errors.** Tools that take exactly one namespace
  (`memory_stats`, `memory_export`, `memory_consolidate` suggest-mode,
  `memory_namespaces update`) now explain, when handed a comma-separated
  value, that CSV lists are only supported by `memory_context`,
  `memory_recall`, and `memory_search`.
- **`memory_store` junk-namespace guard.** Storing with a comma-separated
  `namespace` now fails fast instead of silently minting a namespace literally
  named `"a,b"` and storing into it.
- **Gingugu logo in the Memory Explorer header.** The repo logo now sits to the
  left of the brain icon in the UI header (`ui/public/logo.svg`).

---

## [0.5.0] - 2026-07-07

### Added

- **`gingugu init` — bootstrap a repo so an assistant actually uses the brain.**
  A new CLI subcommand that installs the memory protocol as *tooling*, not just
  documentation. For **Claude Code** (default) it writes a `SessionStart` hook
  that auto-injects the startup contract into context every session (a rules
  file is not guaranteed to be loaded; a hook is), a `Stop` hook that enforces
  save-discipline, and the `/sink-the-ship` session-end command — then wires
  both hooks into `.claude/settings.json`, merged **non-destructively** (existing
  config is backed up to `settings.json.bak` and preserved). The project
  namespace is derived from the repo folder name. Idempotent; `--dry-run`
  previews, `--force` overwrites. For Windsurf / Cursor / Cline (no hook system),
  `--client windsurf|cursor|cline` writes the matching rules file with the
  protocol block instead. Closes the gap where the project's own install (hooks)
  was far more capable than the copy-paste setup shipped to users.
  - Also appends the runtime artifacts the hooks generate (`logs/`,
    `.claude/data/`, `.claude/settings.local.json`, hook `__pycache__/`) to the
    target repo's `.gitignore`, non-destructively — so a session transcript
    never lands in the repo, which matters most on a public one.
  - Output is themed: a 90s h@x0rZ boot-sequence (ASCII banner + `[ OK ]` log),
    which degrades to clean monochrome when piped or `NO_COLOR` is set.

---

## [0.4.0] - 2026-07-07

### Added

- **`gingugu serve` — run the memory server over the network.** A new CLI
  subcommand exposes the same MCP server over **streamable HTTP** (the current
  MCP transport, which supersedes the legacy HTTP+SSE) so a hosted/central
  instance can be reached remotely; `gingugu` with no arguments still runs over
  stdio. Access is gated by a **Bearer token** (`MEMORY_SERVE_TOKEN`): if unset,
  a token is read from `<db-dir>/serve_token`, or generated, saved `0600`, and
  printed — the server never starts open, and the token is stable across
  restarts without any external secret store. A `/healthz` endpoint is exempt
  for load-balancer probes. New env vars: `MEMORY_SERVE_HOST` (default
  `127.0.0.1`), `MEMORY_SERVE_PORT` (default `8765`), `MEMORY_SERVE_TOKEN`.
- **`MEMORY_CREDENTIALS_ENABLED` flag (default `true`).** Set `false` to run an
  instance without the four `credential_*` tools — for a shared/central server
  that should not expose a secret vault (also sidesteps the headless-keyring
  problem on serverless/Pi/Jetson hosts).
- **`gingugu promote` — promote local "gold" up to a central brain.** A new CLI
  (an MCP *client*; the server stays a pure store) reads a source instance,
  keeps only durable org-knowledge, stamps provenance, and stores it into a
  central instance — idempotently (re-runs skip already-promoted memories). The
  filter is exclusion-based, not type-gated: it keeps `verified` memories minus
  episodic/personal tags, and **refuses to promote any content that looks like a
  live secret** so a shared brain never becomes a credential leak. Tokens come
  from `GINGUGU_SOURCE_TOKEN` / `GINGUGU_TARGET_TOKEN`; `--dry-run` reports what
  would move without writing. Read-only on the source.
- **Multi-namespace `memory_context`.** The `namespace` parameter now accepts a
  comma-separated list (e.g. `"crow,my-project"`): one call loads every
  namespace and **de-duplicates memories that surface in more than one** -
  previously, loading N namespaces at session start returned the same
  high-scoring cross-namespace patterns N times. The response carries
  `namespaces` + `duplicates_removed` (single-namespace calls keep the
  historical `namespace` key), and every returned memory is stamped with its
  home `namespace` name. `limit` applies per namespace.
- **`compact` mode on `memory_context`.** `compact=true` replaces each
  memory's full `content` with a whitespace-normalized ~200-char `summary`
  excerpt and drops bookkeeping fields - a 5-10× lighter session-start payload.
  Pull the full body with `memory_recall` when a memory matters.
- **Review hints for point-in-time memories.** A memory like "PR #947 open,
  waiting on Joe" is true at write time and silently wrong once the PR merges.
  New `staleness.py` detector flags in-flight phrasing (open-PR references,
  waiting-on/blocked-on, unmerged branches) on memories not confirmed within
  14 days, plus self-dating signals that fire immediately (`expires
  <past-date>`, stale `as of <date>`). Surfaced as advisory `review_hints` on
  `memory_context` results and a namespace-wide `review` block (count +
  sample) in `memory_stats`. Purely informational - never-forget stands; the
  caller reconciles with `memory_update`/`memory_forget`.
- **Suggest mode on `memory_consolidate`.** Call it without `memory_ids` for a
  **read-only** near-duplicate scan of a namespace: pairwise embedding
  similarity over stored vectors (threshold `min_similarity`, default 0.9 -
  tuned on a real brain; lower values cluster by topic, not duplication),
  union-found into candidate clusters with ids, titles, and peak similarity.
  Only current-generation (modal-dim) embeddings are compared; stale-model
  vectors are reported in `skipped_stale_model`. Falls back to exact-title
  clusters when embeddings are absent or sparse. Nothing is written - inspect
  the clusters, then call again with `memory_ids` to consolidate. Scan is
  capped at 1000 memories per namespace (O(N²), vectors normalized once).
- **Save-discipline Stop hook (`.claude` kit).** `stop.py --check-memory-saves`
  blocks the stop **once per session** when the transcript shows substantial
  tool activity (default ≥15 calls) but zero gingugu memory writes, with a
  reminder to save before the session's knowledge is lost. Second stop always
  goes through - a nudge, not a cage. Wired into `.claude/settings.json`.

### Changed

- **Context loads no longer count as accesses.** `memory_context` refreshes
  each surfaced memory's dormancy clock (`last_accessed`) but no longer bumps
  `access_count` or writes `access_log` rows - those are reserved for
  `memory_recall` / `memory_search` hits. Mandatory session-start loads were
  inflating the access component of the composite score, a rich-get-richer
  loop where whatever already ranked high got auto-loaded, credited, and
  ranked higher still.

### Fixed

- **Memory Explorer timeline: honest activity chart.** The "Access activity"
  chart summed each memory's lifetime `access_count` at its `last_accessed`
  bucket, piling a memory's whole history into its newest bucket - with the
  new context-load semantics that read as phantom recent activity. Now
  "Recently active": each memory counted once at its last-touched date.
- **`metadata` now accepts a JSON object, not only a JSON string, on
  `memory_store` / `memory_update`.** Over HTTP transports the MCP layer
  delivers a JSON-object argument as a dict, so the `str`-only parameter
  rejected it — structured `metadata` was unusable for any remote client. The
  handlers now coerce a dict/list back to JSON text for storage.

## [0.3.8] - 2026-06-24

### Added

- **`memory_store` and `memory_update` now suggest relation candidates.** When
  storing or updating a memory, the response includes a `suggested_relations`
  list of up to 3 existing memories with moderate topical overlap that aren't
  already linked — a non-blocking nudge to call `memory_relate` and grow the
  knowledge graph. Distinct from the existing `similar_memories` hint:
  `similar_memories` flags merge candidates (high overlap, score ≥ 0.5),
  `suggested_relations` flags link candidates (moderate overlap, score ≥ 0.3,
  with already-related and already-similar memories filtered out). New
  `relation_check: bool = True` param on both tools; set `False` for bulk
  imports. `memory_update` skips the check when only tags or confidence
  changed (matching surface didn't change).

## [0.3.7] - 2026-06-16

### Fixed

- **`memory_context` no longer evicts freshly-stored memories at session
  start.** The auto-context engine built a union of three intent buckets
  (task-relevant, recently-active, cross-namespace patterns) then collapsed
  them into a single composite-score sort capped at `limit`. Because the
  composite is relevance- and confidence-dominated, a memory saved in the
  previous session (never accessed, `access_count=0`) was out-ranked by older,
  heavily-accessed memories and pushed past the cut — so "where we left off"
  context silently vanished. `build_context` now uses **guaranteed per-bucket
  quotas**: each bucket is ranked by its own native signal and reserves a share
  of the slots, filled recency-first (`ceil(limit × 0.3)`), then task relevance
  (`ceil(limit × 0.5)`), then cross-namespace (3). Remaining slots are
  backfilled by composite score. Only `context.py` changed; `memory_recall`
  and `memory_search` ranking are untouched.

## [0.3.6] - 2026-06-16

### Added

- **Ollama embedding backend.** Set `MEMORY_EMBEDDINGS_BACKEND=ollama` to
  delegate all embedding calls to a running Ollama process via its HTTP API
  instead of loading the fastembed ONNX model in-process. Zero extra memory
  footprint — Ollama uses whatever embedding model it already has loaded.
  Configure with `MEMORY_EMBEDDINGS_OLLAMA_MODEL` (default: `nomic-embed-text`)
  and `MEMORY_EMBEDDINGS_OLLAMA_HOST` (default: `http://localhost:11434`).
  Gracefully falls back to `NullEmbeddingProvider` if Ollama is unreachable at
  startup. No new dependencies — uses stdlib `urllib.request`.
- **`*.zip` added to `.gitignore`.**

## [0.3.5] - 2026-06-16

### Added

- **Near-duplicate hint on `memory_store`.** Every store now returns a
  `similar_memories` list of up to 3 existing memories in the same
  namespace whose content/title strongly overlap with the new one. The
  store always proceeds — this is a non-blocking signal so the caller can
  choose to consolidate, relate, or update instead of accumulating
  near-duplicates. Set `dedupe_check=False` for bulk imports.
- **Hygiene block on `memory_stats`.** `compute_stats` now includes a
  cheap, SQL-only `hygiene` summary surfacing the easy-to-detect cleanup
  candidates that the manual namespace-scan workflow looks for first:
  ghost namespaces (zero memories) and exact-title duplicate clusters
  within a namespace. Lets the agent decide *at session start* whether a
  deeper sweep is warranted, without auto-deleting anything.

### Fixed

- **Access Activity chart in Memory Explorer was always 0.** `memory_recall`,
  `memory_search`, and `memory_context` returned memories without ever
  crediting the access — `_record_access` was only reachable through
  `MemoryStore.get()` and every callsite passed `record_access=False`. Added
  a bulk `MemoryStore.record_accesses(ids)` primitive (one batched
  `INSERT` into `access_log`, one batched `UPDATE` that bumps
  `access_count` and refreshes `last_accessed`) and wired it into all three retrieval handlers for
  the seeds they actually return. Spreading-activation neighbours still go
  through `touch_many` (refresh dormancy clock without inflating counts),
  preserving the never-forget model.

### Changed

- **`__version__` is now read from installed package metadata.**
  `gingugu.__version__` was hardcoded to `0.1.0` and silently drifted from
  the real PyPI version. Now resolved via `importlib.metadata.version` so
  it stays in sync with `pyproject.toml` automatically and falls back to
  `0.0.0+unknown` when running from an uninstalled source tree.
- **Release workflow now auto-creates GitHub Releases.** `release.yml`
  extracts the matching `CHANGELOG.md` section for the pushed tag and
  publishes it as a GitHub Release alongside the PyPI upload, so the
  Releases page stays in sync with PyPI without a manual step.
- **Comparison matrix scannability + transparency pass.** Every cell
  in `README.md`'s "How It Compares" table now follows a consistent
  glyph + qualifier convention (`✅ / ⚙️ / ❌` plus a short note)
  instead of mixing verdicts and descriptive sentences. Dropped the
  `Temporal graph validity` row (we don't compete there - inflating
  ourselves with `partial` was dishonest). Sharpened the
  `No LLM call to store a memory` row to honestly state each product's
  default write behavior. Moved `Local visual memory inspection` up
  next to the local-first cluster where it belongs.

## [0.3.4] - 2026-06-15

### Changed

- **Comparison matrix rewritten for factual fairness.** `README.md`'s
  "How It Compares" section now uses an edition-aware 7-column matrix
  (`Gingugu | OpenMemory MCP | Mem0 OSS | Mem0 Platform | Graphiti
  (OSS) | Zep Cloud | Letta`) instead of bucketing OSS, managed,
  and MCP variants into one cell per product. OpenMemory MCP is now
  correctly marked local-first; Letta is credited for its ADE visual
  inspector; the conflated "knowledge graph built-in" row is split
  into *typed memory relations* and *auto entity / relation
  extraction* so Graphiti's actual lead on extraction is honest.
  Framing copy reset to the real Gingugu lane (one inspectable local
  memory layer for a developer using several coding agents — no cloud
  account, no agent framework, no graph DB, no LLM call to store a
  memory) rather than overstating differentiation.

## [0.3.3] - 2026-06-15

### Fixed

- **Broken relative links on PyPI.** All relative links in `README.md`
  (`LICENSE`, `SECURITY.md`, `docs/architecture.md`, `docs/enterprise-vision.md`,
  `docs/future-architecture.md`, `examples/mcp_config.json`, `.windsurfrules`,
  `CHANGELOG.md`) now point to absolute `https://github.com/gingugu/gingugu/blob/main/...`
  URLs. PyPI doesn't ship the repo's file tree alongside the rendered
  README, so relative links 404'd there. Works on GitHub and PyPI now.

## [0.3.2] - 2026-06-15

### Added

- **Pre-migration backups.** When `migrate()` is called with a known DB
  path and there are pending migrations, the live DB file is now copied
  to `<db>.bak-before-vN` (where N is the first pending target version)
  before any schema change runs. Skipped for in-memory DBs and first-time
  creation. Best-effort: if the copy fails (disk full, permissions) the
  migration still proceeds with a logged warning. Existing backups for the
  same target are never overwritten — preserves the only known-good copy
  if a previous attempt failed mid-flight. `database._backup_before_migration`.
- **Metadata JSON-object validation.** The `metadata` field is now
  validated as a JSON object on both `create` and `update`. Invalid JSON
  raises `ValueError`; non-object shapes (arrays, scalars, `null`) are
  rejected. Valid input is canonicalized via `json.dumps(..., sort_keys=True)`
  so equivalent payloads are stored identically — helps deduplication and
  prepares the column for the structured provenance fields planned in
  `docs/future-architecture.md`. `storage._normalize_metadata`.
- `SECURITY.md` documenting the threat model, vulnerability reporting,
  and the **agent-mediated credential exposure** boundary (the OS
  keychain protects credentials from disk access, not from a process
  the keychain has authorized — i.e. Gingugu itself when an agent calls
  `credential_get`). Recommends treating the vault as a developer-
  convenience feature, not a production secret store.
- `docs/future-architecture.md` — vision document for the post-v0.3
  direction: epistemic governance layer, structured provenance,
  memory-layer separation (episodic / working / semantic / procedural),
  proposal-flow writes, memory-packet recall, embedded runtime mode,
  and the convergence story with ForgeSmith (epistemic + execution
  governance).
- 13 new tests covering migration backup behavior (5) and metadata
  validation (8). Suite: **151 passing** (was 138).

### Changed

- README and gingugu.com claim sweep — *"production-ready"*,
  *"free forever"*, *"never hit a wall"*, *"nobody else hits all
  three"*, and *"actual brain"* softened to honest framing
  (*"usable today"*, *"zero ongoing cost"*, *"should hold up well"*,
  *"that mix is rare in this space"*, *"structured long-term brain"*).
  Marketing was one version ahead of the operational proof; this aligns
  the public framing with what the code can actually demonstrate.

### Audit notes (no code change)

- Reviewed the access-frequency reinforcement-loop concern raised in
  external review. Confirmed already mitigated: `decay.access_score`
  is log-scaled with saturation at 50 accesses, and `MemoryStore.touch_many`
  (spreading activation) explicitly does **not** increment `access_count` —
  it only refreshes `last_accessed`. Bounded by the `w_access=0.10` weight
  in the composite. No change shipped; documented here so the next reviewer
  knows it was considered.

## [0.3.0] - 2026-06-14

### Added

- **Hybrid search: BM25 + local semantic embeddings.** Recall now fuses
  FTS5 BM25 ranking with cosine similarity over local embeddings using
  **Reciprocal Rank Fusion (RRF)**. Embeddings live in a new
  `memory_embeddings` SQLite table (migration `v4`) — one row per memory,
  packed float32 BLOB.
- **Embedding provider via `fastembed` (PyTorch-free).** Ships ONNX
  runtime (~50MB) + `BAAI/bge-small-en-v1.5` (~80MB, 384 dims) by default.
  Total semantic-search footprint stays under ~150MB instead of the ~2GB
  PyTorch tax. Model loads lazily on first encode.
- **Startup embedding backfill.** New servers run a small backfill batch
  on launch so existing memories pick up semantic search automatically
  after upgrade. Subsequent writes embed inline.
- New env vars `MEMORY_EMBEDDINGS_ENABLED` (default `true`) and
  `MEMORY_EMBEDDINGS_MODEL` (default `BAAI/bge-small-en-v1.5`). Disabling
  the provider degrades gracefully to rank-based BM25-only.
- `EmbeddingProvider` Protocol + `NullEmbeddingProvider` /
  `FastEmbedProvider` impls (`src/gingugu/embeddings.py`) — swapping
  backends is a one-file change.
- 20 new tests covering the embeddings module, RRF fusion, hybrid search
  ordering, dim-mismatch filtering, and storage integration via a
  deterministic `FakeEmbedder`. Suite: **138 passing** (was 118).

### Changed

- **BM25 ranking compression fixed.** The composite score's `relevance`
  term now derives from **rank-based** RRF (1/(60+rank)) rather than the
  old `normalize_bm25` score (which compressed all decent matches into a
  narrow band near 1.0, letting freshness/confidence outrank clearly
  more-relevant memories). `normalize_bm25` is retained for backward
  compatibility but no longer drives search ordering.
- `MemoryStore.__init__` accepts an optional `embedder: EmbeddingProvider`.
  Defaults to `NullEmbeddingProvider` so existing call sites are unchanged.
- `search.search()`, `search.advanced_search()`, and `context.build_context()`
  accept an optional `embedder` kwarg and forward it through. All MCP
  handlers (`memory_recall`, `memory_search`, `memory_context`) pass
  `ctx.store.embedder` automatically.
- README restructured for HN/Reddit launch: install leads with `pip install
  gingugu`; phase-language status replaced with a production-ready callout;
  added "How It Compares" table (mem0, Zep, OpenMemory MCP, Letta, built-in
  tools) and an FAQ section.
- Schema bumped to `user_version = 4`.

## [0.2.0] - 2026-06-13

### Changed

- **Memory lifecycle reframed — dormancy, not decay (never-forget model).** A
  robot brain shouldn't auto-forget; time alone no longer destroys trust or
  retrievability. Concretely:
  - `freshness` now has a **floor of 0.35** (`floor + (1-floor)·exp(-λ·days)`)
    so ancient memories asymptote toward the floor instead of zero.
  - Default scoring weights rebalanced toward **trust**: confidence `0.20 → 0.35`,
    freshness `0.25 → 0.10` (relevance/access unchanged); default
    `MEMORY_DECAY_LAMBDA` `0.05 → 0.01`.
  - **Auto-staleness removed.** `stats.flag_stale` (which demoted aged memories
    to `stale` confidence) is gone; `memory_stats(flag_stale=…)` is now a
    deprecated, ignored no-op. `memory_stats` reports `dormant_count` (with a
    `stale_count` back-compat alias) — a resting signal that never mutates
    confidence.
  - The UI **Decay Heatmap** is now the **Trust Map**: color reflects
    confidence-led trust (with the freshness floor), and a separate clock badge
    marks dormant memories (90+ days untouched) instead of folding dormancy into
    the health color.

### Added

- **Spreading activation.** Recalling a memory (`memory_recall` /
  `memory_context`) now reactivates its relation neighbours (1 hop) — refreshing
  their `last_accessed` so they leave the dormant set — without inflating
  `access_count` or writing an `access_log` row. A dormant memory wakes when a
  related memory sparks it. Backed by the new `MemoryStore.touch_many()`.
- **Memory Explorer UI — Phase 5 polish**: graph hover highlighting (connected
  nodes/edges glow, the rest dim out), search + multi-faceted filter
  (text, type, namespace, confidence), zoom-to-fit, layout sliders (node size,
  link distance, repulsion), auto-refresh interval (Off/5s/30s/1m), promoted
  Timeline to a top-level tab with day/week/month granularity, and the
  **Trust Map** view that grades every memory on a confidence-led composite and
  groups them by namespace/type/confidence, flagging dormant memories at a
  glance.
- **Static-mode dump CLI** `ui/dump_static.py` — writes the live DB to
  `ui/src/data/sample.json` for one-command static refresh / GitHub Pages
  builds.
- **GitHub Pages workflow** `.github/workflows/ui-pages.yml` — auto-deploys
  the UI on every `main` push (or via `workflow_dispatch`) using `VITE_BASE`
  for repo-scoped hosting.
- **Two-layer memory convention** (`crow` + project): a global `crow`
  namespace for cross-project identity, preferences, and meta-learnings,
  loaded at session start before any project namespace. Project namespaces
  remain repo-scoped for schema decisions, bug history, and deploy quirks.
  Documented in README's *Configure Your AI Agent* section and the
  workspace `.windsurfrules` Memory Protocol (v1.2). No schema changes —
  this is a usage convention layered on the existing namespace system.
- **Cross-platform support**: Windows-aware default DB path
  (`%LOCALAPPDATA%\gingugu\memories.db` via `platformdirs`); macOS/Linux keep
  `~/.local/share/gingugu/`. Cross-platform CI matrix (Ubuntu, macOS, Windows ×
  Python 3.11–3.13).
- **Memory Explorer UI** (`ui/`): React app (Vite + TailwindCSS + TypeScript)
  for visualizing memory data. Knowledge graph (force-directed, colored by type,
  tag connections, relation edges with animated particles), dashboard (stats
  cards, type/namespace/confidence charts, tag cloud, timeline, recent memories),
  live data via Python API server that reads the SQLite DB directly through the
  portability module. Falls back to embedded sample JSON when API is offline.
- **Namespace CRUD**: `memory_namespaces` tool (list/create/update/delete) with
  delete guards for the `default` namespace and non-empty namespaces (cascade
  opt-in).
- **Export/import**: `memory_export` / `memory_import` tools for JSON
  dump/restore of namespaces, memories, tags, and relations (credentials
  excluded — secrets live in the keychain). Import re-binds by namespace *name*
  with `skip`/`replace` conflict handling; enum values validated before insert.
- **Relationship graph**: `memory_relate` tool with 6 relation types
  (supersedes, related_to, caused_by, contradicts, parent_of, child_of),
  idempotent directed edges, undirected traversal. `memory_recall` gains
  `include_related` for linked-memory traversal.
- **Consolidation**: merge / summarize / deduplicate strategies with
  `keep_originals` (deprecate + `supersedes` link, or hard delete).
- **Tag system**: tag CRUD with normalization + de-duplication; all-required tag
  filter in `memory_recall`, `memory_search`, and `memory_store`.
- **Decay scoring**: composite additive scoring (relevance × freshness × access
  × confidence) with null-safe freshness, tunable weights via env vars, and
  staleness detection (90d stale / 180d deprecation suggestion).
- **Auto-context**: `memory_context` 3-bucket retrieval (task-relevant +
  recently active + cross-namespace verified patterns) with type boosts for
  architecture and decision memories.
- **Credential Vault**: service-bundle credential store with OS-native secret
  storage via `keyring` (macOS Keychain, Windows Credential Locker, Linux
  Secret Service). 4 tools: `credential_store`, `credential_get`,
  `credential_list`, `credential_delete`. Expiry tracking, `is_secret`
  field-level flag.
- **Advanced search**: `memory_search` with type/confidence/date/tag filters
  and sort_by (relevance, created, accessed, decay_score).
- **Health metrics**: `memory_stats` with counts, staleness reports, namespace
  breakdown, credential health. Opt-in `flag_stale` for non-destructive
  staleness auto-flagging.
- **Memory lifecycle**: `memory_update` (content/title/confidence/metadata/tags),
  `memory_forget` (deprecate or hard delete).
- `MEMORY_DEBUG` env switch for DEBUG logging.
- MCP config template (`examples/mcp_config.json`).
- End-to-end integration test over the full 16-tool MCP surface.
- Hardening: concurrency tests (8 writers, WAL + `busy_timeout`), adversarial
  input tests (FTS5 injection, unicode, 5k-word content), and schema upgrade
  migration tests. **112 tests passing**.
- **16 MCP tools total.**

### Changed

- Docs are **client-agnostic**: README *Configure Your MCP Client* section with
  instructions for Windsurf, Claude Code, Claude Desktop, Cursor, Cline, and
  any generic stdio MCP client.
- Docs cover cross-platform secret storage (macOS Keychain, Windows Credential
  Locker, Linux Secret Service — all via `keyring`).
- `include_stale` renamed to `include_deprecated` on `memory_recall` /
  `memory_search` — the flag only toggles deprecated memories (stale ones are
  always included).
- `memory_update` accepts `metadata=""` to clear stored metadata.
- Runtime dependencies carry upper version bounds.
- `memory_stats` uses `CredentialVault.health` (deduplicated logic).
- `access_log` retention prunes opportunistically on both `memory_stats` calls
  and write ops (throttled to once/hour).

### Fixed

- **Natural-language recall returned nothing**: FTS5 tokens were joined with
  implicit AND, so any query word absent from the corpus zeroed results. Now
  joined with OR; BM25 ranks partial matches.
- **`memory_context` dropped tags**: handler never called `load_tags`, so
  context results came back with `tags=[]`. Fixed.
- **`memory_context` type boost applied twice**: architecture/decision boost
  was +0.2 instead of +0.1. Fixed.
- **Credential vault didn't degrade on keychain failure**: `credential_get` now
  returns metadata + non-secret fields with `value=null` and `"unavailable":
  true` when the keychain is locked/unavailable.
- **Import safety**: `memory_import` validates enum values before insert.
- **Search correctness**: `created_after`/`created_before` and minimum
  confidence filter now apply in SQL before `LIMIT`.
- **Read-only tools creating namespaces**: `memory_recall`, `memory_search`,
  and `memory_stats` no longer create namespaces as a side effect of querying.
- **Orphaned tags**: garbage-collected on retag, delete, and replace-mode import.
- **Dead config**: `MEMORY_AUTO_CONTEXT_LIMIT` was loaded but never used;
  `memory_context` now defaults to the configured value.
- Scoring formula corrected from multiplicative to additive (negative-BM25
  convention would have flipped ranking direction).

---

*This changelog will be updated on every user-visible change going forward.*
