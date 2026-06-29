# gingugu

> A local MCP server that gives AI coding assistants persistent, structured, searchable long-term memory. One SQLite file, no cloud, no telemetry.

---

## What This Repo Does

- Exposes a **Model Context Protocol (MCP)** server (stdio transport) that any
  MCP client (Claude Code, Claude Desktop, Cursor, Windsurf, Cline, …) can use
  as long-term memory.
- Stores memories in a single local **SQLite** database (FTS5 full-text +
  semantic embeddings) at the platform data dir — never inside the repo.
- Organizes memory in **two layers**: a global `crow` namespace (identity,
  cross-project wisdom) and one project namespace per repo.
- Models memory as a **graph** — memories link via typed relations, and recall
  uses spreading activation to wake related context.
- **Never forgets**: dormancy is a resting signal, not deletion. Only explicit
  `memory_forget` removes anything.
- Ships a **Memory Explorer** web UI (`ui/`) for browsing the graph and stats.

**Not** a cloud service. No backend servers, no API keys, no accounts.

---

## Tech Stack

- **Python** `>=3.11`; CI matrix ubuntu/macos/windows × 3.11–3.13
- **MCP** Python SDK (`mcp>=1.25,<2`), stdio transport
- **SQLite + FTS5** (WAL mode); semantic embeddings for hybrid retrieval
- **platformdirs** for the cross-platform DB path
- **uv**-managed; `ruff` + `black`; `pytest` + `pytest-asyncio`
- **UI:** React + Vite + Tailwind (`ui/`), `ui/api.py` backend
- Released to **PyPI** via Trusted Publishing (OIDC) on git tag

---

## Module Map (`src/gingugu/`)

| Module | Responsibility |
|---|---|
| `server.py` | MCP server entrypoint; tool registration; must never crash |
| `config.py` | Config + cross-platform DB path (platformdirs) |
| `database.py` | Connection, schema, WAL, migrations (`PRAGMA user_version`), FTS5 triggers |
| `models.py` | Memory / namespace / relation data models |
| `storage.py` | Memory CRUD (store, update, forget) |
| `search.py` | Hybrid BM25 (FTS5) + semantic ranking |
| `embeddings.py` | Semantic vector generation |
| `context.py` | Session priming (`memory_context`) + spreading activation |
| `relations.py` | Typed graph edges between memories |
| `consolidation.py` | merge / summarize / deduplicate clusters |
| `decay.py` | Dormancy as a resting signal — never auto-forgets |
| `stats.py` | Health stats (counts, confidence, dormancy, hygiene) |
| `namespaces.py` | Namespace CRUD |
| `credentials.py` | OS-keychain credential vault |
| `portability.py` | Export / import a namespace |
| `handlers/` | MCP tool handlers: `memory.py` (store/update/forget), `recall.py` (recall/context), `search.py`, `relations.py`, `admin.py`, `credentials.py`, `helpers.py` |

---

## MCP Tool Surface

- **Memory:** `memory_store`, `memory_update`, `memory_forget`, `memory_recall`,
  `memory_search`, `memory_context`, `memory_stats`
- **Graph:** `memory_relate`
- **Lifecycle:** `memory_consolidate`, `memory_export`, `memory_import`,
  `memory_namespaces`
- **Credentials:** `credential_list`, `credential_get`, `credential_store`, `credential_delete`

`memory_store` / `memory_update` return non-blocking `similar_memories` (merge
candidates, score ≥ 0.5) and `suggested_relations` (link candidates, score ≥ 0.3) hints.

---

## Storage Model

- One SQLite file (default `~/.local/share/gingugu/memories.db`, platform-aware).
- `memories` table + FTS5 virtual table kept in sync by triggers.
- Each memory: `type` (fact/decision/pattern/bug/architecture/preference/workflow/context),
  `confidence` (verified/inferred/stale/deprecated), namespace, tags, timestamps,
  access count, content/title.
- Relations table: directed typed edges (`supersedes`, `related_to`, `caused_by`,
  `contradicts`, `parent_of`, `child_of`).
- Schema versioned via `PRAGMA user_version`; migrations additive by default.

---

## Release State

- Current version: **0.3.8** (PyPI). Public repo `gingugu/gingugu`.
- Two-layer namespace convention (`crow` + project) is live.
- See `.ai/plans/status.md` for in-flight work and carry-overs.
