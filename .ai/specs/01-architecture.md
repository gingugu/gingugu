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
                              ui/api.py ──────────┘  (read-mostly Memory Explorer)
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

## Retrieval

- `memory_recall` blends **BM25** (FTS5 lexical) with **semantic** similarity
  (embeddings), combined with recency, confidence, and access frequency.
- `memory_context` is the session-priming entrypoint: top-N by relevance to a
  task hint, plus spreading activation into related memories. Accepts a
  comma-separated namespace list (one call per session, de-duped across
  namespaces) and a `compact` mode (title + excerpt). Context loads refresh
  the dormancy clock but don't count as accesses - `access_count` is a pure
  recall/search usage signal.
- `memory_search` is the precision path: explicit filters (tags, type, date,
  confidence) and sort order.

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
- **Never-forget over decay.** Biological-style decay was removed because the
  product promise is "your AI never forgets"; dormancy + spreading activation
  preserves recall quality without deleting history.
- **Hints, not gates.** `similar_memories` / `suggested_relations` nudge the
  caller toward merges/edges but never block a write.
- **Server resilience over strictness.** Handlers fail soft (structured errors)
  so a bad call never takes down the client's memory layer.

## Future Direction

See `docs/future-architecture.md` — the long-term vision is epistemic governance
(versioned claims backed by evidence) and an embedded cognitive runtime that
wraps model invocation with automatic recall + capture. Roadmap-only, not current work.
