# Changelog

All notable changes to Gingugu will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

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
