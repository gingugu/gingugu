# Product Spec

## What it is

Long-term memory for AI coding assistants, delivered as a local MCP server.
The promise: **your AI never forgets** — context, decisions, and lessons persist
across sessions, repos, and tools, in one local SQLite file you own.

## Who it's for

Developers using MCP-capable AI clients (Claude Code, Claude Desktop, Cursor,
Windsurf, Cline, …) who want their agent to retain knowledge between sessions
without a cloud service or API keys.

## Differentiator

Tool-siloed history (e.g. a single client's chat history/projects) does not
carry across tools. Gingugu is **cross-tool** and **local** — the same brain
follows the user from one client to another. Validated in practice: the same
Gingugu instance serves Windsurf and Claude Code against one DB.

## Tool Surface (feature status)

| Tool | Purpose | Status |
|---|---|---|
| `memory_store` | Persist a memory (+ similar/relation hints) | ✅ Shipped |
| `memory_update` | Mutate an existing memory | ✅ Shipped |
| `memory_forget` | Deprecate / delete (only removal path) | ✅ Shipped |
| `memory_recall` | Hybrid BM25 + semantic retrieval (multi-namespace CSV, total-limit; compact mode) | ✅ Shipped |
| `memory_search` | Precision retrieval with filters (multi-namespace CSV, total-limit; compact mode; fetch by exact `ids`) | ✅ Shipped |
| `memory_context` | Session priming + spreading activation (multi-namespace, deduped; compact mode; not access-credited) | ✅ Shipped |
| `memory_stats` | Health: counts, confidence, dormancy, hygiene, review sweep (`review_limit` enumerates all flagged) | ✅ Shipped |
| `memory_relate` | Build typed graph edges | ✅ Shipped |
| `memory_consolidate` | merge / summarize / deduplicate + read-only near-dupe suggest scan | ✅ Shipped |
| `memory_export` / `memory_import` | Back up / transfer a namespace | ✅ Shipped |
| `memory_namespaces` | Namespace CRUD | ✅ Shipped |
| `credential_*` | OS-keychain secret vault | ✅ Shipped |
| `suggested_relations` hint | Nudge edge creation at store time | ✅ Shipped (v0.3.8) |
| Memory Explorer UI | Browse graph + dashboard | ✅ Shipped |
| `gingugu ui` (launcher) | One command serves the built UI + live `/api/export` on one port (no Node); `--dev` for Vite hot reload. Bundle ships in the wheel | 🔧 Built (v0.9.0, pending release) |
| `gingugu serve` (transport) | Run over streamable HTTP + Bearer auth (hosted/central) | ✅ Shipped |
| `MEMORY_CREDENTIALS_ENABLED` flag | Run an instance without the credential vault | ✅ Shipped |
| `gingugu promote` (client) | Promote local gold → central brain (filter + provenance, idempotent) | ✅ Shipped (Stage 1) |
| `gingugu init` (bootstrap) | Install SessionStart+Stop hooks + `/sink-the-ship` (Claude Code) or a rules file (`--client`); non-destructive settings merge | ✅ Shipped |

## Principles

- **Local-first, private.** No cloud, no telemetry, no accounts. One SQLite file.
- **Never forget.** Dormancy is rest, not deletion. Only explicit forget removes.
- **Two layers.** `crow` (who the agent is) vs project namespaces (repo facts).
- **Graph over list.** Memories connect; recall spreads through the connections.
- **Hints, not gates.** Surface merge/link candidates; never block the user.
- **Resilient.** The server never crashes the client's memory layer.

## Out of Scope (today)

- Multi-user / team sync and per-user RBAC. (A single hosted instance is now
  possible via `gingugu serve` behind one shared Bearer token, but multi-tenant
  auth and selective local→central knowledge promotion are roadmap.)
- Cloud storage / managed service.
- Auto-truth / unattended belief governance (see `docs/future-architecture.md` — roadmap).

## Roadmap

Tracked in `docs/roadmap.md`. Near-term: UI doc polish, positive-path relation
tests. Longer-term (Phase 6): hybrid RRF
retrieval, structured provenance, epistemic governance, embedded cognitive runtime.
