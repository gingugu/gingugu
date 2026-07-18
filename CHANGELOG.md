# Changelog

All notable changes to Gingugu will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed

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
