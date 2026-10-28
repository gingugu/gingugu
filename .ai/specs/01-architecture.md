# Architecture

## Overview

Gingugu is a single-process **MCP server**. By default an AI client spawns it
over **stdio**; it can also run over **streamable HTTP** (`gingugu serve`, gated
by Bearer tokens - the owner's, or per-client scoped ones) so a hosted/central instance is reachable remotely. It owns
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
                              webui.py ───────────┘  (read-only Memory Explorer; `gingugu ui`;
                                  │                      ui_api.dispatch on a mode=ro connection)
                                  └──► remote brain's GET /ui/api/*  (when a remote brain is on)
```

## Layers

1. **Transport** - `server.py` registers MCP tools and routes calls to handlers.
   It is the crash boundary: no exception escapes to the client. Two transports
   share this path: **stdio** (default) and **streamable HTTP** via `serve.py`
   (`gingugu serve`), which wraps the same server in a Starlette app with
   Bearer-token auth middleware, a `/healthz` probe, `POST /token/derive` and,
   given the server's context, the owner-only `POST /hook/*` routes and the
   owner-only `GET /ui/api/*` Explorer read routes (`serve_ui.py`); the
   middleware resolves each token to a grant and a kind (see **Scoped tokens**
   and **Remote mode** below). The `credential_*` tools
   are gated by `MEMORY_CREDENTIALS_ENABLED` so a shared instance can omit the
   secret vault. `ServerContext.transport` records which transport built the
   server (`"stdio"` / `"http"`); `credential_get(into=…)` refuses under HTTP,
   where a caller-named path would be a file write on the server's disk.
2. **Handlers** (`handlers/`) - thin adapters that validate input, call the core
   modules, and return structured dicts. Split by domain: `memory`, `forget`,
   `hints`, `recall` (+ `context_merge`, the pure merge of per-namespace context
   loads: pins first, then ranked tails interleaved by rank), `search`, `stats`,
   `excerpt`, `relations` (+ `relation_ops`),
   `consolidate`, `dream`, `admin`, `xray`, `credentials`, `tripwires`, `board`, `capability_view`, `fence`
   (the scoped-token policy and the stdio `MEMORY_GRANT` policy, `stdio_grant`; see Scoped tokens and Warm minions), plus `summaries` (payload
   shapes: full summary, compact summary, the picker between them), `choices`
   (enum argument parsing shared by every write-surface field with a controlled
   vocabulary), `helpers` and `scope` (the read scope recall and search share:
   all namespaces when unconfigured, widen an empty scoped lookup to every
   namespace).
3. **Core** - `storage`, `search`, `embeddings`, `embedding_text`, `chunking`,
   `context`, `relations`, `consolidation`, `decay`, `stats`, `namespaces`,
   `portability`, `capability`, `grants` (the per-call grant every chokepoint
   consults), `serve_tokens`, `tripwire` (the memory-tripwire decision half of the `PreToolUse` hook; its other half is
   `xray/edit_hook`; see Key Decisions), `board` + `board_events` (the agent board store and its
   `subscriptions/listen` bus; see Key Decisions), `board_delivery` (the post/reply
   delivery receipt: can the recipient persona's known clients read the namespace),
   `xray/` (Codebase X-ray: `scan` + `extract` + `lang/` (one adapter per language: `python`
   on `ast` with `resolve`; `typescript` + `ts_*` + `jsonc` and `golang` on tree-sitter) +
   `model` + `local` on the client, `validate` +
   `rank` + `store` on the brain, plus `edit_hook`, the hook side that builds the graph itself and never
   touches the brain; see Key Decisions), `proxy_xray` (the proxy's local half of `code_xray`),
   `board_watch` +
   `board_watch_remote`, `board_watch_emit` (the `gingugu board watch` wake-up command; see Key
   Decisions), `board_hook` (board mail for the prompt hook; see Key Decisions), `cli_usage` (the top-level help text), `minion_fence`, `subagent_hook` and `subagent_warmup` (see Warm minions),
   `recall_pick` (the recall pipeline the local hook and `/hook/recall` share),
   `hook_remote` + `hook_remote_flows` and `serve_hooks` (see Remote mode), `hook_grant`
   (the client's `MEMORY_GRANT` as the hooks apply it), `credential_cli` (`gingugu
   credential list|delete`: the vault by hand, for a person at a terminal), `confirm` (the user's
   confirmation for destructive tools; see Key Decisions), `proxy_vault_link`
   (the local vault behind an in-memory MCP session; see Remote mode), `client_grants` (each server
   publishes its grant for its client's hooks; see Warm minions), `claude_trust`
   (whether Claude Code trusts a folder, for the untrusted-minion warning),
   `ui_api/` (the Explorer's read-only observer API: `dispatch` + the
   `known_route` allowlist over a `mode=ro` connection; see Key Decisions),
   `serve_ui` and `webui_remote` (its two transports: `/ui/api/*` on a brain, and
   `gingugu ui`'s forwarder to it; see Remote mode). `storage` owns the `memories` row only; the satellite tables
   it drags along have their own owners (`tags`, `access`, `embedding_sync`,
   `claim_sync`), reached through the `storage_derived.DerivedTables`
   delegation surface, which also carries `log_query` for `query_log` (owned by
   `query_log.py`; per-query rather than per-memory, so not a satellite). They
   are modules over a bare connection rather than methods on `MemoryStore`
   because `portability.import_data` writes memory
   rows too, and an invariant locked inside that class is one the import path
   cannot honor. `embedding_text.embedding_input()` is the one text recipe
   every write path and every compare path shares - split out of `embeddings`
   because it is a contract between callers, not provider code.
4. **Persistence** - `database.py` owns the SQLite connection and its PRAGMAs
   (WAL, foreign keys, busy timeout). The schema itself lives in the
   `migrations/` package: `schema.py` for structural work (tables, columns,
   FTS5 triggers), `claim_derivation.py` for migrations that only re-read
   existing prose, `runtime.py` for coordination tables that describe the
   processes touching the store rather than the memories in it (`activity`,
   `dream_lock`), `fields.py` for write-time declared fields on the memory
   record (`provenance`, `about`) and the FTS5 rebuild that indexes them,
   `queries.py` for the query log (`query_log`, migration 015),
   `tripwires.py` for the tripwire triggers (`tripwires`, migration 016),
   `board.py` for the agent board (`board_posts`, `board_reads`, migrations 017, 018, 020),
   `xray.py` for Codebase X-ray (`xray_snapshots`, `xray_files`, `xray_edges`, migration 019;
   `xray_files.lang`/`kind`, migration 021), and
   `__init__.py` for the ordered registry and the runner. `config.py` resolves
   the DB path.

## Memory Model

- **Three namespace layers:** `crow` (shared by every persona: the user's rules,
  preferences, cross-project lessons) + an optional persona namespace named by
  `MEMORY_PERSONA` (an agent's own self: reflections, its own failure modes,
  opinions) + one per project. Loaded in that order. Every memory belongs to
  exactly one namespace.
- **Typed memories:** `type` ∈ {fact, decision, pattern, bug, architecture,
  preference, workflow, context, capability}; `confidence` ∈ {verified,
  inferred, stale, deprecated}. A `capability` carries `metadata.capability` =
  `{run, path?}` (see `capability.py`).
- **Write-time declared fields (migration 014):** `provenance` ∈
  {user-asserted, measured, file-derived, self-concluded} - how the writer
  came to believe the memory, enforced at the application layer rather than a
  SQL `CHECK` (SQLite cannot alter a constraint in place). Orthogonal to
  `confidence`: a `verified` memory can still be `self-concluded`. `about` -
  what the memory is for, in the user's own words - is indexed on both sides
  of hybrid search: a `memories_fts` column, and part of the embedding text
  via `embedding_text.embedding_input()`. Both are `None` until declared, and
  neither advances `last_confirmed`.
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
  assert nothing about truth and leave the clock alone. `updated_at` is a write
  clock with its own discipline (2026-10-17): it moves only when a stored field
  (title, content, type, confidence, metadata, provenance, about) changes
  value, so a pin, a tag set, a claim resolution or a no-op moves neither the
  recency bucket nor this anchor.
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

  It is the first of two hook-driven paths. **Tripwires** are the second: a
  `PreToolUse` hook that reacts to what the agent is about to *do* rather than
  to what the user typed (see Key Decisions).

  The split is `recall_gate.py` (pure arithmetic, no I/O, unit-testable
  without a brain) and `recall_sweep.py` (everything that touches the world),
  with `prompt_hook.py` as the entry point. For a persona agent the entry point
  also checks board mail on every prompt, before the length gate (`board_hook`,
  see Key Decisions). The same seam as `graph_stats` and
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

  The sweep stays read-only. The hook's one write is `log_prompt`, called after
  selection and before the early return so a prompt that surfaced nothing is
  still a row: it records the affect-stripped prompt and the ids actually
  injected to `query_log` (`tool='hook'`) over its own short write connection
  (`mode=rw`, never creates a file, 0.5s busy timeout), swallowing every
  exception so it cannot cost the turn its injection, and skipping silently on
  a store not yet at migration 015. `recall_sweep.sqlite_uri` builds every
  `file:` URI from the resolved path, percent-encoded - a raw path containing
  `#` or `?` ended the filename early and dropped `mode=`, letting SQLite fall
  back to read-write-create.

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
what*. `session.current_session_id` reads `inbound.current()` - a ContextVar
gingugu's own middleware sets per message - rather than taking a `Context`
handler argument, so no retrieval handler signature and no published tool
schema changes for a bookkeeping field.

The key names the client's conversation on its transport: one id per process
on stdio (one process per client), and on `gingugu serve` a SHA-256 digest of
the `Mcp-Session-Id` header the SDK minted at `initialize` - a process-level id
there would manufacture co-access between clients that never shared a
conversation, and the digest keeps a live session handle out of the DB. The
session *object* stopped being usable as a key with SDK 2.x, which builds a
fresh `ServerSession` per message. A 2026-07-28 HTTP client is sessionless by
design and gets `NULL`.

Outside a request the value is `NULL`, never a placeholder: "unknown" is true,
where "these belong together" would not be.

`query_log.session_id` carries the same id, so a question joins to the memories
it returned in that session. Rows written by the involuntary-recall hook carry
Claude Code's session id instead - a different namespace of ids, so hook rows
do not join to `access_log`.

Note the ceiling this inherits. `access_log` is pruned to a rolling 90-day
window, so co-access is a moving picture. Anything built on it has to keep its
own durable aggregate. `query_log` is the opposite case: it is never pruned,
because nothing summarises it - the rows are the only copy.

## Transactions

Nearly every write here is a single statement that commits itself, which is the
right default: a store call should be durable when it returns. `consolidation`
is the exception - it is `1 + 2N` writes (create, then a `supersedes` edge and a
retirement per original), and a partial application is worse than none. Under
`keep_originals=False` the retirement is a hard delete, so a failure halfway
through the loop destroys memories the surviving record never absorbed. That
hard delete is also the one the user confirms through the client before it
runs (see the Key Decisions entry on confirmations).

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
  recall/search usage signal. A given `task_hint` is still recorded in
  `query_log` (`tool='context'`). **Pinned** memories (`memory_update(pinned=True)`)
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
- **Every query is recorded.** `memory_recall` always, `memory_search` when it
  carries a `query` (an `ids` fetch or filter/sort listing is not a question),
  `memory_context` when given a `task_hint`, and the involuntary-recall hook:
  one `query_log` row each, ids in rank order, zero-hit queries included.

## Scoped tokens (the grant fence)

`gingugu serve` accepts the owner token (full access, unchanged) plus scoped
tokens from `gingugu token add NAME --ns gingugu=write,crow=read`. A **grant**
(`grants.py`) maps namespace names to `read` or `write`; `*` matches any.

```
Authorization: Bearer <token>
  → BearerAuthMiddleware (serve.py): owner token → FULL; derived → DerivedTokens
    (in memory); scoped → TokenStore (SHA-256 lookup in serve_tokens.json);
    none → 401; sets request.state.grant and request.state.token_kind
  → tool wrapper (handlers/__init__.py, the _HeartbeatMCP proxy):
    fence.request_grant reads the grant off this call's own HTTP request
    (inbound.current(), set per message by inbound.middleware), fence.refusal
    closes whole-brain tools, then
    grants.bind(grant) scopes the call
  (a confirmation resolver, `confirm.py`, runs BEFORE this wrapper, so it
   checks the fence itself via `confirm.fenced` before asking the user)
  → handler → store chokepoints consult grants.current()
```

**Why the fence sits at the store, not in handlers.** A handler-level check sees
the arguments a caller *names*; the leaks are in what a call *reaches*. Id-only
calls (`memory_update`, `memory_forget`, `memory_relate`) name no namespace.
Widening reruns a scoped search across every namespace. Spreading activation
and `include_related` walk edges into other namespaces. Context buckets
aggregate across namespaces. Each of those funnels through the same few store
functions, so the grant is enforced there: `NamespaceManager`
(get/list/get_or_create/resolve_name/update/delete), `search_common.build_filters`
(feeds both the BM25 and semantic pools), `search_listing.fetch_by_ids`,
`MemoryStore` get/create/update/delete, tags and `resolve_claims`,
`record_accesses`/`touch_many`, `RelationManager` (an edge write needs write on
**both** endpoints), the relation repair ops, `cross_namespace_patterns`,
`capability.lane`, tripwire load/list/add/remove, and the global block of
`memory_stats` (scoped: namespace inventory only, no log or credential counts).

**Semantics.** Anything outside a grant reads as *not found*, never
*forbidden*: no existence oracle. An omitted namespace means the grant's own
namespaces for recall/search/stats/context; a tool that needs exactly one
namespace under a grant that cannot read the server's default errors
`namespace is required for this token` without naming the default. Widening stays
inside the grant. A granted but not-yet-existing namespace is created on first
write. stdio without `MEMORY_GRANT`, CLI commands, background passes (dream,
hooks) and the owner token are unfenced. No schema change; no MCP tool
signature changed.

## Remote mode (stdio proxy and derived tokens)

Shipped in 0.21.0 (#99-#103). When
`gingugu remote on` or
`MEMORY_REMOTE_URL` selects a brain, a bare `gingugu` (`server.main`) does not
open the local DB: `proxy.py` relays MCP JSON-RPC between its stdio and
`gingugu serve`, so client configs stay `command: gingugu`.

```
client ⇄ stdio ⇄ proxy ──POST /token/derive (owner token, once + refresh)──▶ serve
                    └─────Bearer <derived token>  /mcp (streamable HTTP)────▶ serve
```

- **Preflight** (`proxy.preflight`, in `proxy_preflight.py`; config refusals first so they never touch the
  keychain or network): malformed, blank or `*=write` `MEMORY_GRANT`;
  `MEMORY_CREDENTIALS_ENABLED` not false together with a `MEMORY_GRANT` (a
  scoped client never gets the vault); no keychain token (points at
  `gingugu remote login`); brain unreachable (`/healthz`, retried for up to
  `REACH_BUDGET_S` = 15s with 0.5s -> 1s -> 2s backoff, no probe after the
  deadline; refusal reads `unreachable after 15s: <url>`, or the reason from
  `remote_tls.diagnose`: for https the TLS failure, for http on a port that
  speaks TLS the `gingugu remote login https://...` to run). The retry exists
  because an MCP client never retries a server that exits at startup, and a
  brain on WiFi can drop the first packet after idle; the budget stays inside
  Claude Code's 30s connect timeout. Plain http to a non-loopback host warns
  on stderr; to a pinned brain it never gets here (`remote.active()` refuses
  it). Exit 2 on refusal.
- **Local vault** (`proxy_vault.py`): the vault is the machine's keychain plus
  its local DB's `credential_*` tables, which a remote brain cannot serve. With
  credentials on (owner only, per preflight), `server.main` hands the proxy
  `config.db_path`; `LocalVault` registers `handlers/credentials.py` on a private
  `MCPServer` over that DB with `transport="stdio"` (so `into=` writes locally),
  bypassing `_HeartbeatMCP` (the grant is always full here and the local brain is
  not in use). `Link.pump` swaps the brain's `credential_*` entries in
  `tools/list` for the local ones; `read_client` hands every `credential_*`
  `tools/call` to `VaultLink.submit` and never forwards it, so the vault answers
  while the brain is down. `LocalVault.screen` refuses `credential_get` with
  `reveal` (and non-object `arguments`) before the vault sees the call (security
  review, 2026-10-04): everything else in the session is relayed, so a steered
  model could otherwise put an inline secret into a brain-bound call; `into`
  cannot. The vault runs as an MCP server over in-memory streams
  (`proxy_vault_link.VaultLink`), handshaken with the client's `initialize`,
  so it sees the client's capabilities and `credential_delete` can ask the user
  through the client: the proxy's confirmation, with
  `MEMORY_CONFIRM_DESTRUCTIVE=off` on the proxy as its opt-out. The brain's own
  elicitations (hard forget, cascade delete, consolidate, import) relay through
  the proxy unchanged, except for a client known to show no dialog (Claude Code's
  VS Code extension): the proxy strips `elicitation` from the `initialize` it
  forwards (`confirm.withhold_dialogs`), so the brain and the vault both refuse
  "cannot ask". A vault that fails to open ends the proxy with a
  `ProxyLost` message. With credentials off, `credential_*` is stripped and
  refused. `memory_stats` is relayed, but its credential summary
  (`global.credentials` or `stats.credentials`) describes the brain's vault, so
  `Link` tracks each `memory_stats` call and `patch_stats` replaces that block
  with `LocalVault.health()` (metadata only, no keychain); an unrecognised reply
  passes through.
- **Derive route** (`serve_derive.py`, `POST /token/derive`): `BearerAuthMiddleware`
  tags each request `token_kind` (owner, derived, scoped); only an owner token
  may derive (others get 403). `DerivedTokens` (`derived_tokens.py`) keeps
  SHA-256 -> (grant, expiry) in memory: TTL up to 1h, at most 256 live (429
  past it), nothing on disk, so a server restart drops them all. The owner token
  never goes on `/mcp`. The derived grant's `origin` is stamped from the
  calling owner token's name (`grants.origin_name`); a caller cannot claim one.
- **Grant additions** (`grants.py`): `Grant.home` is the namespace a call that
  names none defaults to, in place of the server's configured one
  (`grants.home()`, read by `NamespaceManager.resolve_name` and
  `handlers/scope.read_scope`); `Grant.derived` marks a minted token, and
  `fence.refusal` closes every `credential_*` to it even when full. The proxy
  asks for the client's `MEMORY_GRANT` (full if unset) and home = the
  client's resolved namespace (`MEMORY_NAMESPACE`, else the `MEMORY_NAMESPACE_PATH`
  basename; may be unset).
- **Resilience** (`proxy.py`, `proxy_session.py`, `proxy_auth.py`): one reader of
  the client for the whole run, routed to the live `Link`. Refresh at half the
  TTL; the token is reused across reconnects (deriving per attempt would fill the
  live cap); backoff 0.25s doubling to 5s; the client's `initialize` is replayed
  on the new session. A restarted brain has forgotten every derived token and
  MCP session, and refuses both before any tool runs, so those requests are
  safe to send again: a 401 renews the token (once per stale token, and only if
  that token was ever accepted or 10s have passed, so a brain refusing every
  fresh token cannot drain the live cap) and retries that request in place
  (`SessionAuth`); a 404 sends it again, once, on the next link - unless it is
  over 10s old, when it is errored instead (the client may have re-sent it). A
  request whose fate is unknown is failed, never replayed (a `memory_store`
  would write twice). A new request waits up to 5s for a
  reconnecting link, then gets an error. A 429 or 5xx from derive is transient; a 401/403 on derive
  ends the proxy (`ProxyLost`). `proxy.serve` (the body of `serve_stdio`) wraps `run` in the
  SDK's `stdio_server`: it closes the write stream when `run` ends - the SDK's stdout writer
  runs until it does - so client stdin EOF exits 0, and it unwraps `ProxyLost` from the
  transport's task group so `server.main` exits 1 with its message, not a traceback. A request that arrives before the client's
  `initialize` (Claude Code's `server/discover` version probe) is answered
  locally with `-32601` and never relayed: the brain 400s any non-initialize
  request without a session, which would read as a lost link and fail the
  `initialize` behind it.
- **Hooks in remote mode** (board #1 B2, `hook_remote.py` client,
  `serve_hooks.py` server): with a remote brain on, the three hooks never open
  the local DB or load a local encoder. They POST to owner-token-only routes on
  `gingugu serve` (`build_app(..., ctx=ctx)`; `server.build` returns the MCP app
  and its `ServerContext`): `/hook/recall` (the brain embeds, sweeps, selects and
  logs the prompt), `/hook/tripwires` (rules only; matching stays local, cached
  60s per URL + namespace set in a 0600 `hook-sessions/tripwire-rules-*.json`,
  the last copy used while the brain is away), `/hook/trip` (the trip's
  `query_log` row), `/hook/warmup` (the agent file's grant, scoped to the
  client's namespaces) and `/hook/inbox` (board mail: a persona's unread posts,
  read-only, fenced by the names of the client's grant via
  `hook_grant.client_grant`; `null` = unfenced). Per-session suppression stays in this machine's
  `hook-sessions` files and is sent with each request, so the brain holds no
  session state. Route work runs in a worker thread: reads on its own read-only
  connection, the only writes the `query_log` rows, never the MCP handlers'
  `ctx.conn`. No answer means a quiet hook, never the local DB; tripwires alone
  keep using their last fetched rules. The routes trust the namespace list they
  are sent, so the client's grant is applied before it is sent:
  `prompt_hook.namespaces_for` drops what the client's grant
  (`hook_grant.client_grant`) cannot read and fails closed (`[]`, no request)
  on a grant `stdio_grant` rejects or a disagreement. The minion warm-up
  narrows the agent file's spec the same way (`hook_grant.narrow_spec`) before
  it is used or sent to `/hook/warmup`. Every name sent to a `/hook/*` route
  passes `hook_remote_flows._sendable` first (the routes' own name rule), so an
  odd folder name is left out rather than 400ing the whole request.
- **Explorer read API in remote mode** (`serve_ui.py` server, `webui_remote.py`
  client): `gingugu serve` answers `GET /ui/api/*` (`info`, `graph`,
  `memory/<id>`, `search`, `stats`, `events`) for owner tokens only (scoped and
  derived get 403, no token 401), by running `ui_api.dispatch` in a worker thread
  on a read-only connection. `gingugu ui` forwards its own `/api/*` there with
  the keychain owner token in the `Authorization` header only, verifying through
  `remote_tls.verify_for`, never following a redirect; the token never reaches
  the browser or an error body. The route allowlist (`ui_api.known_route`) runs
  before any forward.

## TLS on the brain (secure off the box, zero-setup on it)

Local (stdio) mode has no network hop and never touches a certificate. A
`gingugu serve` bound to loopback stays plain http. A serve bound to anything
else gets TLS by default (`serve_tls.resolve`, `MEMORY_SERVE_TLS=auto`): a
self-signed EC P-256 leaf generated once in `<db-dir>/tls/` (key 0600) and
reused, naming the hostname, `<host>.local`, loopback, a specific bind IP and
`MEMORY_SERVE_TLS_SAN`. `MEMORY_SERVE_TLS_CERT` + `_KEY` bring your own;
`MEMORY_SERVE_TLS=off` opts out; a bad setting refuses to start.

```
remote login ──ssh: cert──▶ forced command ──▶ <db-dir>/tls/serve_cert.pem (public)
             ◀── PEM ──────┘   pinned at <user-data-dir>/remote-tls/<host>_<port>.pem
             ──ssh: mint───▶ machine token ──▶ keychain, under the https URL
proxy / derive / hooks / status ──https, verify = the pin alone──▶ serve
```

- **Trust rides SSH.** The login already authenticates the brain by its SSH
  host key (trust-on-first-use, `StrictHostKeyChecking=accept-new`, so the pin
  is as good as that first connection), so the cert it hands over on that
  channel is the brain's. Nobody
  copies a cert file. An older brain refuses the verb (exit 2): no TLS, http
  kept.
- **Nothing rotates until TLS is settled** (`remote_tls.prepare_login`, from
  the security review): `cert`, then a live `GET /healthz` over https with the
  exact trust clients will use (`probe`), then the pin is written, and only
  then `mint`. The SSH session does not run with the service's environment, so
  `cert` can hand over a cert the brain no longer serves; the probe catches
  that, and a cert that misses the dialled host, with the fix in the message.
  An https login with no cert offered drops a stale pin (a brought cert); an
  http login to a pinned brain is refused.
- **A TLS failure says so.** `proxy_session.derive` turns a certificate failure
  into `ProxyLost` with `remote_tls.explain`'s fix rather than an endless
  "reconnecting" - except a clock failure (not yet valid, expired), which stays
  transient and retried; preflight's unreachable refusal and `remote status`'s `hint:` line carry
  `diagnose`'s reason. An http URL on a TLS port is named too: `speaks_tls`
  completes an unverified handshake (a ClientHello only, no request, no token)
  and `diagnose` says to log in again over https.
  A corrupt pin raises (never default trust), and the forced command re-encodes
  the cert it prints, so nothing but certificate bytes leaves the brain.
- **The pin is the only trust anchor** (`remote_tls.verify_for`:
  `create_default_context(cadata=pin)`, hostname checking on). A CA:FALSE leaf
  verifies as its own anchor on 3.11 and under 3.13's strict mode, and cannot
  sign anything else. With no pin, https uses the default store, so a brought
  publicly-trusted cert works without one.
- **A pin means TLS.** `remote.active()` and `remote on` refuse http to a
  pinned host:port, so a stale `MEMORY_REMOTE_URL=http://...` fails loudly
  instead of sending the token in the clear.
- **A brought cert is never pinned**: a real cert renews every few months and
  a pin on it would break every client. `cert_pem` offers only the generated
  one, judged from the forced command's own environment; if a generated cert
  is left on disk after the service moved to a brought cert or TLS off, `cert`
  offers it, the login probe fails, nothing rotates, and the message says to
  delete `<db-dir>/tls`. Regenerating the generated cert means `remote login` again.
- Five call sites verify through the pin: `proxy_auth.brain_client`,
  `proxy_session.derive`, `hook_remote.post`, `remote._reachable`, and
  `promote._session`.

## Warm minions (stdio grant, warm-up, minion fence)

A subagent gets its own fenced brain and arrives already knowing the task.

- **`MEMORY_GRANT`** (`config.grant`, `handlers/fence.stdio_grant`,
  `ServerContext.stdio_grant`): a stdio server started with
  `MEMORY_GRANT="ns=read|write,..."` runs under that grant through the same
  chokepoints as a scoped token - whole-brain tools closed, outside the grant
  reads as not found. Unset is the full grant. Parsed before the database
  opens; a bad spec or `*=write` raises and `main` exits 2, so a typo can never
  fall back to the whole brain.
- **The hooks follow the server's grant** (`client_grants`, `hook_grant`). A
  hook runs in the client's environment, not the MCP server's `env` block, so
  the server publishes what it runs under: `main` writes
  `{"grant": MEMORY_GRANT|null, "role", "created", "chain": [[pid, start], ...]}`
  to `<user_runtime_dir>/gingugu/clients/<pid>.json` (0600, `O_EXCL|O_NOFOLLOW`
  temp + replace, owner-checked) before serving, stdio and proxy alike. A
  hook's client is the lowest common ancestor of hook and server - launchers
  (`uv run`, `sh -c`) sit on one side only, and two sessions under one host
  meet further up - so a hook takes the publications that meet its chain at
  its nearest ancestor, and never above its own client (the nearest ancestor
  with a child off the hook's chain): a meeting at a shared shell or host is
  another session's server. Every link is a `(pid, start time)` pair (start
  rounded to 10ms), so a reused pid never matches, and no process names are
  matched. Live publications
  apply, `role: minion` skipped only beside a non-minion one. The hook's own
  `MEMORY_GRANT` and every publication must agree, or the hook refuses (quiet,
  logged); so does an unreadable or foreign-owned publication, or an
  unreadable process tree. A publication is withdrawn only on a clean
  shutdown - a normal return, or the SIGTERM/SIGINT a client stops its servers
  with (measured: Claude Code left every file behind until `release_on_signal`;
  the handler withdraws, restores the default and re-raises, so the exit is
  unchanged) - and a fenced server that crashed, lost its brain or refused a bad
  grant leaves its file as a fence, and a client with a fence and no live
  server refuses (unfenced dead ones are ignored). Publishing sweeps dead
  files: unfenced at once, a fence once nothing above pid 1 in its chain is
  alive. A young client (nearest ancestor with a child off the hook's chain,
  under 3s old) is waited on for the whole window: measured on Claude Code
  2.1.301, the server was spawned 51ms before SessionStart finished and imports
  for ~300ms. Nothing published at all falls back to the hook's own
  environment. Residuals (two security passes): a fenced server slower than
  3s on its very first start, or one that cannot write its publication (it
  serves fenced and logs; the hook then most likely cannot read the dir
  either); a hook launcher with two children of its own adds the wait; a main
  server that inherited the minion mark, beside a second unfenced server under
  the same client, is skipped; a fence left by a kill (no clean shutdown)
  stays until its tree is gone, binding only its own dead session.
- **Fenced agent files** declare an inline `brain` stdio server with
  `MEMORY_GRANT`, `MEMORY_CREDENTIALS_ENABLED=false` and
  `MEMORY_SERVER_ROLE=minion`, list `mcp__brain` in `tools`, and put
  `mcp__gingugu` in `disallowedTools`: a subagent otherwise inherits the
  parent's full-brain server. Claude Code loads a PROJECT agent file's inline
  server only once the folder is trusted (`~/.claude.json`
  `projects[<dir>].hasTrustDialogAccepted`); untrusted, the minion starts with no
  `brain`, so the warm-up says so and `gingugu harness` warns (`claude_trust`).
- **Warm-up** (`subagent_hook.py`, `SubagentStart` doorway): `SubagentStart`
  has no task prompt, so the `PreToolUse` hook stashes the spawning `Agent`
  call's prompt (`hook-sessions/spawns-<session>.json`, 300s TTL, 0600, only for
  fenced agents) and the
  warm-up takes it as `task_hint`. Ranking is `build_context` on a read-only
  connection under `grants.bind`; compact lines go out as `additionalContext`.
  No grant in the agent file, no warm-up. The agent file's grant is first
  narrowed to what the spawning client's own `MEMORY_GRANT` can read
  (`hook_grant.narrow_spec`, a `*` entry kept); nothing left means no warm-up.
- **Minion fence** (`minion_fence.py`, first thing in `tool_hook.run`): only
  when the payload has `agent_id`. Denies file tools on the data dir (and
  Grep/Glob rooted at or above it), shell
  commands naming the data dir, the database file, the sqlite CLI or the
  gingugu CLI in command position (a speed bump), and the write and
  `credential_*` tools of the inherited `mcp__gingugu__` server (reads open),
  which also covers built-in agents such as Explore. Own switch
  `MEMORY_MINION_FENCE=off`, independent of `MEMORY_TRIPWIRES` and of
  `MEMORY_XRAY_TRIPWIRE` (the X-ray edit check's switch). The tripwire
  doorway passes `mcp__gingugu__*` through when `agent_id` is set so the fence
  decides.
- **Threat model:** prevents accidents, not a hostile same-user process. An OS
  sandbox and an audit-trigger undo log were considered and declined.

---

## Key Decisions
- **Codebase X-ray: the client reads the checkout, the brain keeps the graph
  (X1, v0.29.0).** `code_xray` maps a repo's import graph. A brain
  under `gingugu serve` may be another host, so it never reads a path: the
  local stdio server or the proxy (`proxy_xray`, like the vault) runs
  `xray.extract` on this machine and sends a payload of repo-relative names
  and edges. *Content-addressed snapshots*: the fingerprint covers every source
  file's bytes, every manifest an adapter reads and `EXTRACTOR_VERSION`, so two worktrees with
  different edits are two snapshots and nothing needs compare-and-set; every
  query fingerprints first (0.07s on this repo) and extracts + ingests only on
  a miss, so results match the working tree without the agent asking. *Full
  ingest, no deltas*: an unchanged file can resolve differently when another
  file appears. *Directed PageRank*, unlike `dream.centrality`: rank flows
  importer -> imported (undirected ranked `server` second). *Implicit edges*
  (parent initializers, and the package in `from pkg import submodule`) count
  for blast radius but not rank; without that an empty `__init__.py` ranked
  first. *Blast radius is never capped* (reverse BFS, paged response). Rank
  orders it but never filters it. Answers say "estimate" and "candidates". Own tables
  (migration 019), never memories; 5 most recently used snapshots kept per
  namespace. *Trust*: the fingerprint is the client's claim and cannot be
  checked; a namespace writer is trusted with its graph as with its memories,
  and an explicit `refresh` replaces a snapshot whose `graph_digest` differs.
  *Coverage is honest*: every source not read is a skip with a reason, and
  only a deliberate one (a symlink) leaves the graph `complete`.
- **Codebase X-ray: one adapter per language (unreleased).** `xray/lang/`
  holds an adapter per language family: the file extensions it parses, the
  manifest basename patterns it needs (`pyproject.toml`, `go.mod`,
  `tsconfig*.json`, `package.json`, `index.html`), and a pure function from
  bytes to files + edges. The scan reads every source and manifest once,
  under the same symlink / containment / size checks, and an adapter never
  touches the disk. *Python stays on `ast`* (the running interpreter's exact
  grammar); TypeScript/JavaScript and Go parse with **tree-sitter** from pinned
  grammar wheels, imported lazily so a Python-only repo never loads a parser.
  Rejected: tree-sitter-language-pack (downloads native parsers on first use,
  no documented integrity check), SCIP (needs a working build, i.e. runs the
  repo's toolchain, and reindexes whole), stack-graphs (archived 2025-09).
  *Package nodes*: Go imports a directory, and an edge to every file in it
  gave bubbletea 2,992 edges for 116 files and handed a big package one
  share of its importer's rank per file. A package is now a node (`kind:
  package`, named by import path): importer -> package (`module`), package ->
  each non-test file (`member`), a file -> its own package (`package`,
  uncertain and implicit, so blast reaches siblings but rank ignores it), a
  test -> its package. PageRank then divides a package's rank across its
  files with no change to `rank.py`; 227 edges on bubbletea. Blast walks
  through packages (`hidden=`) and never lists one. *Containment*: the
  extractor drops a dangling or repeated edge (counted unresolved) and the
  scan skips a name the brain would refuse (not UTF-8, a backslash), since
  `validate` refuses a payload whole over one. Migration 021 adds
  `xray_files.lang`/`kind` with defaults true for every older row, and a
  payload without them still ingests.
- **Codebase X-ray: tripwire on edit (X2, v0.29.0).** The first
  Edit/Write/MultiEdit of a source file an X-ray adapter parses that other files import is denied once
  per file per Claude Code session, in `gingugu hook tool`. The reason lists
  how many files import it and how many candidate tests, the top 5 dependents
  by rank with hop counts, up to 5 candidate tests (`test_*.py` first, nearest
  first) and an "import-based estimate" caveat; the re-issued edit passes.
  *Deny, not inform-after*, for the reason memory tripwires deny: a
  `PreToolUse` `additionalContext` lands next to the tool result, after the
  edit. *Local, no brain round trip*: the hook builds the graph itself from the
  git checkout with the same extractor as `code_xray` and caches it per repo by
  content fingerprint, so it works the same with a remote brain. Measured on
  this repo (356 `.py` files): 1.46s for the whole hook process on the first
  edit with a graph rebuild, 0.5s cached. State lives beside the DB in
  `hook-sessions/`: `xray-graph-<sha16(root)>.json` (cache) and
  `xray-shown-<session>/` (one claim file per shown file, created atomically
  with `O_EXCL` so parallel hooks cannot lose a record); owner-only 0600,
  swept after 7 days untouched. *Silent by construction*: a file
  nothing imports, a new file, a file no adapter parses, a path outside a git repo, a
  missing session id or any failure passes; it denies only once its "shown"
  state is saved, so a failed write can never block an edit forever. File
  names with control characters are quoted in the deny text. Own switch
  `MEMORY_XRAY_TRIPWIRE` (default on), independent of `MEMORY_TRIPWIRES` and
  `MEMORY_MINION_FENCE`. Order in `tool_hook.run`: minion fence, X-ray, memory
  tripwires; when a memory tripwire and the X-ray both fire on one call they
  share one deny (tripwire reason, blank line, X-ray reason). X-ray denies are
  not logged to `query_log`.
- **Destructive tools ask the user, not the model (unreleased).** Five paths
  confirm through MCP elicitation (SDK 2.x `Resolve` + `Elicit`): a hard
  `memory_forget`, a cascading `memory_namespaces` delete of a non-empty
  namespace, `memory_consolidate` with `keep_originals=False`, a
  `memory_import` with `on_conflict="replace"` that would overwrite existing ids
  (the dialog names the count), and `credential_delete`. The logic lives in
  `confirm.py`. *The answer stays out of the input schema*: it is a resolved
  parameter, so the model cannot supply it; a plain `confirm=true` argument was
  the model's own say-so and protected nothing. `credential_delete`'s `confirm`
  stays as an optional, model-side no (`confirm=false` refuses outright). *Refuse
  when nobody can be asked*: decline, cancel (what a headless client answers),
  and a client without elicitation each return a structured `ok: false`; an
  unrecognised answer refuses too. Claude Code's VS Code extension declares
  elicitation but answers every one "decline" without a dialog, so it counts as
  unable to ask: `confirm.shows_no_dialog()` (env `CLAUDE_CODE_ENTRYPOINT` in
  `_SHOWS_NO_DIALOG`) makes `can_ask` false, but only on a stdio server - over
  HTTP the env says nothing about the remote client, so `ask`/`can_ask` take the
  executing server's transport. In remote mode the proxy strips `elicitation`
  from the `initialize` it forwards and stores (`withhold_dialogs`), so the brain
  and the local vault refuse the same way. The refusal can carry a `by_hand`
  pointer: `credential_delete`'s names `gingugu credential delete NAME` (new
  `credential_cli.py`), which asks at a real terminal - it refuses a non-TTY
  stdin and has no `--yes`, stopping accidents and piping, not an agent with a
  shell. *The env opt-out sits on the server that
  executes the tool*: `MEMORY_CONFIRM_DESTRUCTIVE=off` (also `false`/`0`/`no`)
  is read straight from the environment, so in remote mode it is the brain for
  memory tools and the local proxy for the vault. *Resolvers run before the
  wrapper*, so they check the fence themselves and are `async`; each body
  re-derives whether the call is destructive and checks existence first, so a
  hidden or out-of-grant id reads "not found" with no dialog. *The proxy's
  vault gets a real MCP session* (`VaultLink`): a direct `call_tool` carries no
  client capabilities and no way back to the client, and the answer arrives on
  the same reader that submitted the call, so `submit` never awaits it and the
  vault's requests go out as `vault-<nonce>:<id>` (a per-process nonce).
- **MCP SDK 2.x, with 1.x's runtime shape kept on purpose (2026-10-18).**
  Three things in the 2.x port are decisions, not renames. *Tools stay on the
  event loop*: 2.x runs a plain `def` tool on a worker thread, and every
  handler shares one sqlite3 connection bound to the thread that opened it, so
  the one tool wrapper (`_HeartbeatMCP`, and the vault's `_OnLoop`) registers
  each tool as `async` - calls stay serialised as under 1.x. Running tools
  concurrently would need a connection per thread and is its own decision.
  *gingugu owns its request scope*: 2.x removed the ambient `request_ctx`, and
  threading a `Context` through every tool would put an SDK type on published
  signatures, so `inbound.middleware` scopes the HTTP request and session id
  to each message for the fence and the access log. The SDK marks middleware
  provisional; `tests/test_inbound.py` pins what is relied on. *Serve keeps
  1.x's limits where 2.x tightened them*: the 4 MiB request-body default would
  refuse a whole-brain `memory_import`, so `serve.mcp_app` allows 64 MiB; and
  change-notification subscriptions are on only for the agent board (a
  `BoardBus`; see the board decision below), where they used to be off.
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
  the brain over HTTP for a hosted/central instance. Its HTTP app is built with the
  real bind host (`MEMORY_SERVE_HOST`, `serve.mcp_app`): a loopback bind keeps the MCP SDK's
  localhost-only `Host` allowlist (DNS-rebinding protection), a LAN bind drops
  it and relies on Bearer auth - left at the SDK default, every LAN client got
  `421`. The owner token keeps full
  access; each additional client can hold a scoped token limited to named
  namespaces (see **Scoped tokens**). It stays a single SQLite file and a
  single owner - scoped tokens partition one owner's brain between clients, not
  tenants - so multi-tenant auth remains roadmap (see `docs/future-architecture.md`).
- **Promotion is a client, not server logic.** `gingugu promote` (`promote.py`)
  speaks the public MCP tool surface to two instances - read-only `memory_export`
  from a local brain, filtered `memory_store` into a central brain with a
  provenance stamp. The server gains no promotion-specific code; the selective
  local→central absorption lives entirely in the client. Keeps the store pure.
- **Onboarding is client-side too.** `gingugu init` (`bootstrap/`) writes no
  server code - it copies packaged templates (`bootstrap/templates/*.tmpl`) into
  a target repo: for Claude Code, a `SessionStart` hook that auto-injects the
  memory startup contract every session (a rules file is not guaranteed to load
  into context; a hook is), a `Stop` save-discipline hook, a `UserPromptSubmit` hook (involuntary
  recall), a `PreToolUse` hook (tripwires, the X-ray edit check, via the
  `.claude/hooks/pre_tool_tripwire.py` doorway), and the
  `/sink-the-ship` skill (`.claude/skills/sink-the-ship/SKILL.md`, the format
  Anthropic's docs steer new work to; the legacy `.claude/commands/` copy is
  retired with a `.bak` only when it is byte-identical to the template we would
  have written, and kept when it differs - the marker says we once wrote a file,
  not that the user left it alone) - plus a `SubagentStart` hook (minion warm-up, `.claude/hooks/subagent_warmup.py`) - merging all five hooks into `.claude/settings.json`
  non-destructively (`settings.py`), allowing `mcp__brain` there and in the
  user-level `~/.claude/settings.json` (`init_user_permissions`) and appending the hooks' runtime artifacts
  (`logs/`, `.claude/data/`, `.claude/settings.local.json`,
  `.claude/hooks/**/__pycache__/`, `.claude/**/*.bak`, `CLAUDE.md.bak`,
  `AGENTS.md.bak`) to the target's `.gitignore` so transcripts never get
  committed (`bootstrap/gitignore.py`; a rerun adds missing rules under the
  block an earlier run wrote, never a second block under the same header). Output is a themed 90s boot sequence
  (`theme.py`, degrades to monochrome off-TTY). Other clients (`--client`) get a
  rules file. This closes the gap where the repo's own hook-based install
  outperformed the copy-paste setup shipped to users.
- **`gingugu harness` is `init` plus the working kit, as a separate command.**
  Plain `init` stays gingugu-only; `harness` (`bootstrap/harness.py`, templates
  under `bootstrap/templates/harness/`) runs `init` and layers on a secrets/`rm`
  guard, one stdlib JSON-lines logger for the remaining hook events (the kit
  it replaces had one near-identical script per event, each rewriting a growing
  JSON array on every call), a pre-compaction transcript backup, three fenced
  minions whose `{{namespace}}` is rendered from the folder name, `/creating-pr`,
  a create-only `.ai/` scaffold, a managed block in the repo `CLAUDE.md` (left
  out when the file already carries its own `## AI Knowledge Base Enforcement`
  section - a repo that wrote its own rules keeps them, without a generic copy
  beside them), and deny rules (`harness_settings.py`). Voice and LLM-calling hooks were left out deliberately: nothing
  installed may call the network or need an API key, and a test enforces it.
  Logs are metadata-only by allowlist: hook payloads carry file bodies,
  command output and prompts, and a security review found the guard logging a
  blocked `.env` write - secret included - before deciding. Transcript backups
  hold the whole session, so they live under `.claude/data/` at 0700/0600.
  It runs `init` with `create_rules=True`, so a missing repo `CLAUDE.md` is
  created there (`# <dirname>` + the protocol block, reported as a new file) and
  the harness block lands in it in the same run; plain `init` never creates one.
  Every report a dry run prints is what the real run then does: identical bytes
  are `no change`, a forced write over an existing file is `would overwrite`.
- **`harness --migrate` / `--prune` move a repo off the older per-event kit in
  two steps, because a running Claude Code session keeps calling the hooks it
  started with and a missing script blocks the prompt.** `--migrate`
  (`harness_migrate.py`) replaces gingugu's own hooks, unwires the kit's
  loggers for `log_event`, and moves each kit script - recognised by name *and*
  fingerprint - into `.claude/hooks/retired/` behind an exit-0 stub, recording
  a sha256 manifest (`harness_retired.py`). After a restart `--prune`
  (`harness_prune.py`) deletes only unedited, unwired stubs and files whose hash
  still matches the manifest; symlinks and anything changed stay. Its
  `--dry-run` lists exactly what the real run removes.
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
- **A long memory gets vectors for what the encoder never read, and cosine never
  picks among them.** The encoder truncates at a fixed token window - 512 for
  bge-small, 510 of content - with no error and no log; measured on a real
  brain, 79.1% of memories are longer than that, so before this the semantic
  half of search had never seen a memory's tail at all. `chunking.py` splits a
  memory into window-sized pieces cut on the encoder's own tokens (migration
  013, `memory_chunks`, additive - the head vector in `memory_embeddings` is
  untouched), and at query time a keyword-pool member is scored on exactly ONE
  piece: the one holding the most distinct query words, ties to the head.
  Scoring by the best-matching piece was measured first and rejected - it is a
  wash, because every long sibling then gets several chances to match any
  query, and the head's matches get buried by the tail's gain. Memories outside
  the keyword pool keep their head vector; measured there, pieces add nothing.
  Only a backend exposing `token_offsets` + `max_tokens` (`FastEmbedProvider`,
  via a no-truncation copy of fastembed's own tokenizer) is pieced - Ollama and
  disabled embeddings stay head-only. `gingugu embed` runs the backfill to
  completion for an existing store instead of one small batch per server
  start. Measured on a real brain copy, 383 position-stratified questions:
  hybrid mrr 0.2675 → 0.284, tail mrr 0.197 → 0.256, head 0.337 → 0.323,
  +7ms/search.
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
  React bundle *and* the live `/api/*` read API from one process on one port, so
  pip-installed users get the Memory Explorer with no repo checkout and no Node.
  The Explorer reads through that API (`ui_api/`); the legacy `/api/export` full
  dump is removed (the route 404s).
  Frontend layout (`ui/src/`): `api/` (client, `useBrainData` polling hook,
  static-mode loader), `brain/` (3D scene: lobes, visuals, camera, bloom),
  `list/` (virtualized table), `detail/` (drawer, markdown body, wiki links),
  `shell/` (header, source badge, deep links), `components/` (Dashboard,
  Timeline, Trust Map on a body-less `MemoryLite`). Pure logic (lobes, visuals,
  table, links, deep links, client) has vitest unit tests beside it. Remote
  images in a memory body are never loaded (alt text shows), so viewing a memory
  makes no third-party request.
  Static demo: `ui/dump_static.py --db <fictional demo db>` runs the real read
  API and writes `ui/src/data/static/{graph,info,details}.json`; on a static
  host (GitHub Pages) `/api/info` fails and the page loads those instead, and
  the source badge reads STATIC demo. CI has a `ui` job (`npm ci`, typecheck, test,
  build; Node 22); `ui-pages.yml` and `release.yml` also use Node 22.
  The bundle is bundled via hatch `force-include` (`ui/dist` → `gingugu/_ui_dist`,
  built in `release.yml` before `uv build`); `webui.find_dist()` falls back to
  `ui/dist` in a source checkout. `--dev` spawns the Vite dev server for hot
  reload. Same-origin serving means the prod path needs no CORS. Static serving
  guards against path traversal and falls back to `index.html` (SPA routing).
- **The Explorer is a pure observer.** `ui_api/` opens the DB `mode=ro` (it never
  migrates or creates), gives no access credit, writes no `query_log` row and
  runs no spreading activation; `/stats` passes `prune=False` so a read never
  prunes `access_log`. Opening the Explorer cannot change the brain it shows.
  No MCP tool-surface change and no schema migration.
- **Owner-only on serve, per-request local or remote on `gingugu ui`.** The API
  shows the whole brain, so `/ui/api/*` takes owner tokens only. `gingugu ui`
  asks `remote.active()` on every request: the local DB through `dispatch`, or a
  forward to the brain. The owner token lives in the keychain and is sent only
  in the forwarder's `Authorization` header, never to the browser.
- **`gingugu ui` is a loopback-only tool with no auth of its own.** `--host`
  must be loopback (exit 2 otherwise); every request needs a loopback `Host`
  header (403 otherwise, against DNS rebinding); unknown `/api` routes 404 from
  an allowlist *before* anything is forwarded (httpx2 resolves `..`, so a
  non-allowlisted path could otherwise make the forwarder a confused deputy);
  no origin is granted CORS (Vite proxies `/api` in dev). Anything that can reach
  the port can read the brain, the remote one in remote mode, so this is a
  single-user-machine tool by design.
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
- **Queries get their own table, and it is never pruned.** `query_log`
  (migration 015) records the question, the scope actually searched (`*` for
  every namespace, including a scoped read that widened), and the returned ids.
  It is not an `access_log` column because `access_log` has one row per
  *returned memory*: it never saw the question, and a miss leaves no row at
  all - and misses are the point. `result_ids` is a JSON array in rank order
  rather than a join table, since it is written once, read whole, and its order
  is the point. Unlike `access_log`'s 90-day window (safe because its aggregate
  lives on `access_count`), nothing summarises `query_log`, so pruning it would
  destroy the only copy. `query_log.record` never commits and swallows
  `sqlite3.Error`: retrieval must not fail over bookkeeping. The hook is the
  awkward writer - its sweep is `mode=ro` by design - so `log_prompt` opens a
  separate short-lived write connection after selection rather than loosening
  the sweep's connection.
- **Tripwires are regex triggers in their own table, and they deny once.**
  (2026-09-30.) Involuntary recall reacts to the prompt, but the costliest
  repeat mistakes happen at a specific action the prompt rarely names. A
  `tripwires` row (migration 016) pairs a memory with a tool-name regex
  (full-matched) and an input regex (searched in the call's match text). Own
  table because one memory can guard several calls and a trigger has its own
  lifecycle; `ON DELETE CASCADE` so a hard-deleted memory takes its tripwires
  with it. Pure regex, no embeddings, no model judgment: a risky command and a
  harmless one can read alike to an encoder. The decision that shaped the hook
  is **deny, not inject**: a `PreToolUse` `additionalContext` is delivered next
  to the tool *result*, after the call ran, so the only way to speak before the
  call is to deny it. The first matching call per session per memory is denied
  with the memory as the reason; the same call re-issued unchanged passes
  (per-session suppression, separate from the prompt hook's state).
  `mcp__gingugu__*` tools never trip, so a bad tripwire can always be repaired
  (inside a subagent the separate minion fence still denies their writes).
  Pinned memories still trip - a pin loaded at session start is not in front
  of the agent at the moment it acts - while deprecated and superseded ones do
  not. `tripwire.load_tripwires` reads SQLite read-only: the local hook calls it,
  and in remote mode the brain calls it inside `/hook/tripwires` while the
  client matches the returned rules (see **Remote mode**). Every
  failure exits 0 silently, which is the normal permission flow, and each trip
  is logged to `query_log` as `tool='tripwire'`. Accepted v1 limitation: after
  context compaction the memory may drop out of context while suppression still
  counts it as shown. The same hook also carries the X-ray edit check, a
  second deny on its own switch (see the Codebase X-ray: tripwire on edit
  decision).
- **The agent board is its own pair of tables, and the author is never an
  argument.** (2026-10.) Agents sharing a brain could only talk by writing a
  memory and hoping the other recalled it. A post is not a memory: it is
  addressed, read once, and a task on it is claimed and finished, so storing
  posts as memories would put chatter into the graph and the ranking.
  `board_posts` and `board_reads` (migration 017; 018 adds `author_origin`; 020 adds `author_agent` and `recipient_agent`) are separate tables; unread is
  the absence of a `board_reads` row, so marking read is an idempotent insert
  and the inbox an anti-join. `kind` and `status` are validated in Python, not
  `CHECK` constraints, so a new kind needs no table rebuild. Threads are one
  level deep. `claim` is a compare-and-set (`UPDATE ... WHERE status='open'`),
  so racing agents cannot both win. The author is derived in
  `handlers/board.py` from what the server knows of the connection: the HTTP
  token's name with `derived:` stripped (the proxy now names its derived token
  for `MEMORY_PERSONA`, falling back to the home namespace), or on stdio
  `MEMORY_PERSONA`, `minion` for a warm minion's server, else `local`. The
  fence is the namespace's through the usual chokepoints (read = see, write =
  post/claim/done); a hidden post reads as "not found". Posting creates the
  namespace on first use, like `memory_store`. Never pruned.
  Wake-up is `subscriptions/listen`: the server is built with a `BoardBus`
  (`board_events`) instead of `subscriptions=False`, and every board write emits
  `notifications/resources/updated` for the recipient's
  `gingugu://board/inbox/{persona}` URI (`.../inbox/*` for broadcasts). Tool
  bodies run synchronously, so `BoardBus.emit` is a sync fan-out the async SDK
  `publish` cannot give. Accepted limit: the SDK's listen handler does not
  authorize subscription URIs, so an authenticated token can receive a bare
  "updated" ping for any inbox (never content; reading it still goes through
  the fence).
- **Waking an agent is a command, not a protocol feature.** (2026-10-26.)
  `subscriptions/listen` tells a client an inbox changed, but MCP has no
  standard way to wake an idle agent and nothing in a harness listens. What
  every harness can do is run a command and read its output, so
  `gingugu board watch` (`board_watch`, `board_watch_remote`, `board_watch_emit`) prints one JSON
  line per new post. Measured, not assumed: Claude Code (terminal and VS Code)
  wakes an idle session on each stdout line of a `Monitor` watch and when a
  background command exits; Codex CLI and the ChatGPT desktop app wake on
  `codex queue --thread <id> --message ...` from outside, and a background
  command exiting does NOT wake Codex. So Claude Code arms `Monitor` on the
  watch (re-armed at its 30 minute deadline), and Codex runs `--exec 'codex
  queue ...'` as a sidecar, the post in `GINGUGU_POST_*` and on stdin. Remote
  mode is pushed (a persona-named session token, `subscriptions/listen` on the
  inbox and `.../inbox/*`, refetch via the `board` tool, 1s to 30s reconnect
  backoff, a refused machine token fatal); local mode has no shared server to
  listen to, so it polls the DB read-only every `--interval` s under the stdio
  grant. It never marks anything read. A delivery cursor per persona and brain
  (`<data dir>/board-watch/<persona>-<sha256(brain)[:12]>.json`, the newest
  delivered `(created_at, id)`) stops a re-armed watcher re-delivering;
  delivered is not read, and `--all` ignores it. Rejected: Claude Code's
  `claude/channel` adapter (needs a dev flag on every launch and is dropped in
  VS Code). A reply with no `to` is addressed to the replied-to post's author
  unless that is the caller, then the most recent other author in the thread,
  then the root's recipient, so an answer reaches the other party (an explicit
  `to` wins). The `text` label was shortened by owner decision to `[gingugu
  board post from another agent]`. A live round trip between Beepboop (Claude Code) and Tyrone
  (ChatGPT desktop) was proven 2026-10-26.
- **Board mail rides the prompt hook, fenced by the client's grant.**
  (2026-10-26.) `board watch` needs a harness that can run a watcher; the
  floor under it is `board_hook`, which `prompt_hook.run` calls on every prompt
  of an agent with `MEMORY_PERSONA`, before the length gate, so a bare "go"
  still gets its mail. Unread posts the session has not been shown are injected
  in the same `additionalContext` block as recalled memories (at most 5, a
  300-char JSON-encoded preview each, with the `board(action="read")` call);
  nothing is marked read. The inbox is fenced by the client's `MEMORY_GRANT`
  (`hook_grant.client_grant`), not the recall namespaces (crow, persona, cwd):
  a reply lives in its thread's namespace, often another agent's. Remote mode
  asks `POST /hook/inbox` (3s timeout); shown ids stay on the machine under
  `board:<session>`. The installed doorway `user_prompt_recall.py` still exits
  on prompts under 40 chars in pure stdlib, except when `MEMORY_PERSONA` is set,
  where a short prompt goes through to the hook (the package load, never the
  encoder). `memory_context` also returns `board: {unread: N}` for a persona
  caller.
- **Board identity is persona + machine (2026-10-26).** The inbox hid every
  post by the reader's own persona, so one persona on two machines (each with
  its own owner token on the brain) could never post to itself. A reader is
  now persona + origin, the NAME of the owner token the session was derived
  from: `/token/derive` stamps `Grant.origin` (a caller cannot claim it), a
  direct owner-token call uses that token's own name, `/hook/inbox` takes it
  from the calling owner token, and stdio and scoped tokens have none.
  `board_posts.author_origin` (migration 018, nullable, never backfilled)
  records it per post. Rule (`board._inbox_where`): a post by your own persona
  is mail only when both origins are known and differ; unknown on either side
  keeps the old rule, so pre-018 posts and local stdio callers behave as
  before. Reply routing (`is_me`/`_reply_to`) compares machine too. Labels and
  watch lines show it ("from beepboop on macbookpro", `origin`,
  `GINGUGU_POST_ORIGIN`). Why machine and not session: the brain-side
  `Mcp-Session-Id` changes on every reconnect and a watcher holds a different
  one than its agent, so session-based filtering would echo an agent's own
  posts back through its own watcher. Replies are routed per agent session
  separately (below).
- **Route replies by agent session, not just persona.** (2026-10.) Several
  sessions of one persona share one inbox, so a reply meant for
  the session that asked reached all of them. A post records `author_agent`
  (migration 020), the hash of Claude Code's `CLAUDE_CODE_SESSION_ID`; a reply to
  a persona stores `recipient_agent` = the `author_agent` of that persona's latest
  post in the thread (`board._route`). Only the hash is stored because the raw
  id is a live handle to a transcript. A routed post shows only to that session
  until it has gone unread for `ROUTE_GRACE_SECONDS` (1 hour), then to every
  session of the persona: exclusive delivery would lose a reply whose session
  ended. Top-level posts, broadcasts and an unknown session are never routed,
  and a reader with no known session sees everything, as before. Routing is not
  an access fence - the namespace grant still is, and reading a post by id is
  unchanged. The session travels as `X-Gingugu-Agent-Session` from the proxy,
  as `agent_session` in `/hook/inbox`, and from the env over stdio.
- **A capability is a memory type with its structure in metadata, and recall
  gives it its own lane.** (2026-10-01.) Memory recorded procedures and never
  artifacts: one integration's REST mechanics were written down four times and
  not one memory said a script existed, so it was rebuilt from the notes. A new
  `capability` type says "this exists, run it like this", distinct from
  `workflow`. Its `{run, path?}` lives under a reserved `metadata.capability`
  key rather than new columns: no migration, no FTS rebuild, and export/import
  carry it unchanged; the cost is a soft schema, held by validation on every
  write (type and block come together or not at all, unknown fields refused).
  Reads stat `path` so a deleted script shows as a dead pointer (`exists:
  false`), never on a non-local transport. Rejected: boosting the type in the
  main composite (unmeasured ranking change) and a separate tool like
  `memory_tripwire` (a capability is 1:1 with its memory, and a two-call write
  goes unused). The lane's 0.68 cosine floor was measured on real recall
  queries; the prompt gate's 0.78 missed most of them.
- **Server resilience over strictness.** Handlers fail soft (structured errors)
  so a bad call never takes down the client's memory layer.

## Future Direction

See `docs/future-architecture.md` - the long-term vision is epistemic governance
(versioned claims backed by evidence) and an embedded cognitive runtime that
wraps model invocation with automatic recall + capture. Roadmap-only, not current work.
