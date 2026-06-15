# Changelog

All notable changes to Gingugu will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed

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
