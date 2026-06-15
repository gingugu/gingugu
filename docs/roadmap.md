# 🗺️ Treasure Map — Gingugu Roadmap

> *X marks the spot, but the journey's where the plunder lives.*

---

## Current Status: **Phase 4 — Integration Complete + Dogfooding Live** ✅

> Sails are set and we've left port: namespace CRUD, JSON export/import, a
> config template, a session-start workflow, debug logging, and a full
> end-to-end test — all green (**112 passing**). **16 MCP tools** live. First
> real usage caught and fixed the AND/OR recall bug; a hardening round
> (concurrency, adversarial input, upgrade migration) followed, then a
> 2-round pre-launch code review. The repo **dogfoods itself**.

---

## Phase 1: Foundation (The Hull) 🚢

Build the core storage engine and basic MCP server skeleton.

| Task | Status | Notes |
|------|--------|-------|
| Project scaffolding (`pyproject.toml`, structure) | ✅ | uv-managed, hatchling build, `[project.scripts] gingugu = "gingugu.server:main"`; `handlers/` is a package |
| `.gitignore`, `LICENSE`, `CHANGELOG.md` | ✅ | Repo hygiene — done in Phase 0.1 patch |
| Config loader (env vars from README) | ✅ | `config.py`: all `MEMORY_*` vars; weights normalized (`w_i / Σw`) with defaults fallback |
| Logging setup (stderr only — stdout is MCP transport) | ✅ | `config.setup_logging` → stderr, `force=True` |
| SQLite database module + migrations | ✅ | `database.py`: FTS5 + sync triggers, `PRAGMA user_version`, idempotent `migrate()` |
| WAL mode + foreign keys on connect | ✅ | `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000` |
| Pydantic data models | ✅ | `models.py`: Memory, Namespace, `StrEnum` types + confidence rank (Relation/Tag deferred to Phase 3) |
| Storage layer (CRUD) | ✅ | `storage.py`: create/get/update/delete + access logging |
| Namespace management | ✅ | `namespaces.py`: get_or_create, list, resolve (auto-detect) |
| MCP server skeleton | ✅ | `server.py`: FastMCP over stdio, DI via `ServerContext` |
| Basic `memory_store` tool | ✅ | Verified end-to-end via `call_tool` |
| Basic `memory_recall` tool | ✅ | FTS5/BM25 → normalized relevance score |
| Unit tests for storage | ✅ | 18 tests across database/storage/search (pytest) |
| GitHub Actions CI (ruff + black + pytest) | ✅ | `.github/workflows/ci.yml` — matrix py3.11–3.13 |

**Milestone:** Can store and retrieve memories via MCP protocol.

---

## Phase 2: Intelligence (The Compass) 🧭

Add search ranking, decay scoring, and auto-context.

| Task | Status | Notes |
|------|--------|-------|
| FTS5 full-text search with BM25 | ✅ | `search.py`: candidate pool re-ranked by composite |
| Scoring algorithm | ✅ | `decay.py`: additive relevance×freshness×access×confidence; freshness floored (never-forget), confidence-led weights |
| Dormancy detection (never-forget model) | ✅ | `is_dormant`/`suggests_deprecation` surfaced in stats as `dormant_count` — a resting signal, never a confidence change. Old auto-flagging (`stats.flag_stale`) **removed**; `memory_stats(flag_stale=…)` is a deprecated no-op |
| Spreading activation | ✅ | Recall reactivates relation neighbours (`MemoryStore.touch_many`) — a dormant memory wakes when a related one sparks it |
| `memory_context` tool | ✅ | `context.py`: 3-bucket union + type boosts |
| `memory_search` tool (advanced filters) | ✅ | type/confidence/date/sort_by (tags land in Phase 3) |
| `memory_stats` tool | ✅ | `stats.py`: counts, dormancy, namespaces, cred health |
| Access logging | ✅ | Phase 1 + opportunistic pruning (throttled) |
| Unit tests for search + decay | ✅ | `test_decay.py`, `test_context.py`, `test_stats.py`, search ranking |
| **Credential Vault** | | |
| `credential_services` + `credential_fields` tables | ✅ | Migration v2, isolated from memory tables |
| `keyring` integration for OS-native secret storage | ✅ | macOS Keychain via `keyring` (spike-verified) |
| `credential_store` tool | ✅ | Bundle upsert, secret fields → keychain |
| `credential_get` tool | ✅ | Retrieve bundle with keychain secret values |
| `credential_list` tool | ✅ | Non-secret fields + expiry status, no keychain hit |
| `credential_delete` tool | ✅ | Service/field removal + keychain cleanup, `confirm` gate |
| Expiry tracking in `credential_list` + `memory_stats` | ✅ | active / expiring_soon / expired |
| Unit tests for credential CRUD + keyring | ✅ | `test_credentials.py` with in-memory keyring backend |

**Milestone:** Intelligent retrieval — relevant memories surface automatically with proper ranking. Universal credential vault operational across all workspaces.

---

## Phase 3: Relationships (The Crew) 🏴‍☠️

Memory linking, tagging, and consolidation.

| Task | Status | Notes |
|------|--------|-------|
| Tag system (CRUD + query) | ✅ | `storage.py` set/add/get/load_tags, normalized; tag filter in `search.py` (all-required) |
| `memory_relate` tool | ✅ | `relations.py` RelationManager; 6 relation types, idempotent edges |
| Relationship traversal in search | ✅ | `memory_recall(include_related=True)` appends linked memories (flagged `via_relation`) |
| `memory_consolidate` tool | ✅ | `consolidation.py`: merge / summarize / deduplicate + `keep_originals` |
| `memory_update` tool | ✅ | content/title/confidence/metadata/tags |
| `memory_forget` tool | ✅ | deprecate (default) or `hard_delete` |
| Cross-namespace pattern sharing | ✅ | Delivered in Phase 2 via `memory_context` bucket 3 (verified patterns) |
| Unit tests for relations + consolidation | ✅ | `test_tags.py`, `test_relations.py`, `test_consolidation.py` |

**Milestone:** Memories form a knowledge graph, not just a flat list. — **Done.**

---

## Phase 4: Integration (Setting Sail) ⛵

Polish, configure Windsurf, and make it the default brain.

| Task | Status | Notes |
|------|--------|-------|
| `memory_namespaces` tool (full CRUD) | ✅ | `handlers/admin.py` list/create/update/delete; delete guards `default` + non-empty (cascade opt-in) |
| `memory_export` / `memory_import` tools | ✅ | `portability.py`: JSON of namespaces+memories+tags+relations (excl. credentials); import remaps by namespace name, skip/replace on conflict |
| Windsurf MCP config setup | ✅ | `examples/mcp_config.json` drop-in template |
| Session-start workflow | ✅ | Removed (superseded by `.windsurfrules` memory protocol) |
| Error handling + graceful degradation | ✅ | Every handler try/excepts to a structured error; `ValueError` guards return clean messages |
| Logging + debug mode | ✅ | stderr-only logging; `MEMORY_DEBUG` convenience switch for DEBUG level |
| End-to-end integration test | ✅ | `test_integration.py`: store→recall→context→relate→search→namespaces→export→import→stats→forget over the live tool surface |
| README final polish | ✅ | Status banner, 16-tool table, config + Windsurf sections refreshed |
| First real usage test | ✅ | Caught the implicit-AND recall bug + `memory_context` tag drop + keychain degradation gap |
| Hardening round | ✅ | Concurrency (8 writers, WAL+`busy_timeout`), adversarial input, v2→v3 upgrade migration — 100 passing |
| **Switchover:** dogfood Gingugu in this repo | ✅ | `.windsurfrules` Memory Protocol + `CHANGELOG.md` updated; self-hosting live |

**Milestone:** Fully operational brain, integrated into daily workflow. — **Done: integrated, field-tested, and self-hosting.**

---

## Phase 5: Enhancements (Plunder) 💰

Future upgrades once the core is battle-tested.

| Task | Status | Notes |
|------|--------|-------|
| Local embeddings (sentence-transformers) | ⬜ | Semantic search upgrade |
| LLM-powered consolidation | ⬜ | AI summarizes memory clusters |
| Memory import/export advanced (selective, encrypted) | ⬜ | Builds on Phase 4 export |
| Auto-generate rules files from patterns | ⬜ | Learned preferences → rules (`.windsurfrules`, `.cursorrules`, `AGENTS.md`) |
| Ranking tuning: BM25 relevance weighting | ⬜ | `normalize_bm25` compresses relevance into a narrow band, so freshness/confidence can outrank a more on-topic memory; surfaced during first usage |
| Web dashboard for browsing memories | ✅ | `ui/`: React knowledge graph + dashboard, Trust Map (confidence-led, dormancy badge), full timeline view, hover highlighting, search/filter, layout controls, auto-refresh, GitHub Pages workflow |
| Tag-based spreading activation | ⬜ | Extend reactivation beyond relation edges to shared-tag clusters |
| Backup/sync strategy | ⬜ | git-backed or rsync |
| Multi-workspace support | ⬜ | Multiple IDE/agent instances |

**Milestone:** The brain becomes genuinely smarter over time.

---

## Phase 6: Cognitive Runtime (The Captain's Chair) 🧭

> *Vision detailed in [`docs/future-architecture.md`](future-architecture.md).*

The reframe from "memory database" to "persistent cognitive runtime
for agents." Crystallized after an external architectural review on
2026-06-14. Phase 6 is multi-release work, not a single sprint.

| Task | Status | Notes |
|------|--------|-------|
| **True hybrid retrieval** (independent BM25 + vector candidates → RRF) | ⬜ | Today's pipeline gates semantic on the BM25 candidate pool. Real fix runs both retrievals independently and fuses the union |
| **Migration auto-backup** (`memories.db.bak-before-vN`) | ✅ | Shipped in v0.3.2 |
| **Access-weight reinforcement-loop fix** (log-scale or cap) | ✅ | Already in place — audited in v0.3.2 (log-scaled with saturation at 50; spreading activation does not increment access_count) |
| **Typed JSON metadata validation** | ✅ | Shipped in v0.3.2 |
| **Structured provenance** on every memory | ⬜ | `created_by`, `client`, `model`, `session_id`, `evidence[]`, `user_confirmed` |
| **Memory-layer discriminator** | ⬜ | episodic / working / semantic / procedural |
| **Proposal flow** for non-trivial claims | ⬜ | Agent proposes → governance accepts/quarantines/rejects → commit with audit trail |
| **Memory packet recall format** | ⬜ | Returns `{claims, hypotheses, procedures, warnings}`, not flat list |
| **Embedded runtime SDK** (`brain.run(model, message, ...)`) | ⬜ | Auto recall + capture around model invocation; MCP becomes one adapter |
| **Property-based + failure-injection tests** | ⬜ | Hypothesis for adversarial inputs; chaos for keyring/disk/migrations |
| **Retrieval evaluation corpus** (Recall@K, MRR) | ⬜ | Currently tuning weights by intuition |
| **Credential vault per-service policy** + interactive approval | ⬜ | Closes the agent-mediated retrieval gap documented in `SECURITY.md` |
| **Convergence with ForgeSmith** (epistemic + execution loop) | ⬜ | The bigger product story |

**Milestone:** Gingugu graduates from "MCP server for AI memory" to
"persistent cognitive runtime that agents wake up inside."

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-08 | Python + SQLite + FTS5 | Local-first, zero-config, fast, extensible |
| 2026-04-08 | Single DB with namespaces | Simpler than per-repo DBs, enables cross-repo patterns |
| 2026-04-08 | MCP stdio transport | Native MCP integration, no HTTP overhead |
| 2026-04-08 | Auto-context on session start | Zero-friction memory retrieval |
| 2026-04-08 | Go big from v1 | Full feature set: decay, relations, consolidation, auto-context |
| 2026-04-08 | Storage at ~/.local/share/gingugu/ | XDG standard, portable |
| 2026-04-12 | Additive scoring (not multiplicative) | Predictable, tunable, survives missing factors — see architecture.md |
| 2026-04-12 | Hand-rolled migrations via `PRAGMA user_version` | Alembic is overkill for single-file SQLite |
| 2026-04-25 | Credential vault: service bundles with `keyring` | Secrets in OS Keychain, metadata in SQLite; fully isolated from memory search/context |
| 2026-04-25 | Global-only credentials (no namespace scoping) | Creds should be universally available across all repos |
| 2026-04-25 | `is_secret` field-level flag (default true) | Lets `credential_list` show URLs/usernames without hitting Keychain |
| 2026-05-02 | Verified FTS5 + keyring spikes before building | De-risked the two external integrations; both passed against SQLite 3.50 / macOS Keychain |
| 2026-05-02 | Pin `mcp>=1.25` (resolved `1.27.2`); use FastMCP | FastMCP stdio API is stable across 1.x→2.x; `uv.lock` pins the exact build |
| 2026-05-02 | `StrEnum` for type/confidence, `datetime.UTC` | Cleaner than `(str, Enum)`; both stdlib 3.11+ (our floor) |
| 2026-05-02 | Handlers register via `ServerContext` DI | Avoids module-global singletons; keeps handler modules testable + under 300 lines |
| 2026-06-04 | Rebrand to **Gingugu** | Unique, memorable, available everywhere (GitHub/PyPI/NPM/.com) |
| 2026-06-04 | Drop migration shims pre-launch | Dead code for every public install |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| DB corruption | High | WAL mode, regular backups, graceful error handling |
| Memory bloat (too many entries) | Medium | Consolidation + deduplication (never time-based forgetting — robot brains keep everything) |
| FTS5 relevance quality | Medium | BM25 tuning, fallback to exact match |
| MCP server crash kills the assistant's flow | High | Robust error handling, never panic |
| Outdated memories mislead AI | Medium | Confidence/trust system + explicit `memory_update`/`memory_forget` (time never auto-demotes) |
| Keychain access failure (locked, missing) | Medium | Graceful error: return metadata without secrets, log warning |
| Credential expiry missed | Low | `credential_list` + `memory_stats` surface expiry; user responsible for rotation |
| Concurrent multi-process writes (multiple workspaces) | Medium | WAL mode + `busy_timeout` + retry on `SQLITE_BUSY`; single-writer serialization is expected, not an error |
| Misconfigured scoring weights (don't sum to 1, or all 0) | Low | Config loader normalizes `w_i / Σw`; falls back to defaults with a warning if `Σw==0` |

---

*Next action: launch (repo public + posts), then Phase 5 (Advanced) — local embeddings for semantic search; BM25 ranking tuning; UI polish. Gingugu is self-hosting.*
