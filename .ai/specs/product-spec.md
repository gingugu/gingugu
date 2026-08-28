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
| `memory_update` | Mutate an existing memory (incl. `type` retype, `pinned` to always-load it, and `resolve_claims` to reconcile a stale claim without editing prose) | ✅ Shipped |
| `memory_forget` | Deprecate / delete (only removal path) | ✅ Shipped |
| `memory_recall` | Hybrid BM25 + semantic retrieval (multi-namespace CSV, total-limit; compact mode; `explain=True` for a per-hit `score_breakdown`) | ✅ Shipped (`explain` unreleased) |
| `memory_search` | Precision retrieval with filters (multi-namespace CSV, total-limit; compact mode; fetch by exact `ids`; `claims="open"\|"contradicted"` to work the reconciliation backlog, `claims="unverified"` to read refs the prose never resolved; `orphans=True` to work the graph backlog; `explain=True` for a per-hit `score_breakdown`) | ✅ Shipped (`explain` unreleased) |
| `memory_context` | Session priming + spreading activation (multi-namespace, deduped; compact mode; not access-credited); pinned tier loads first, additive to `limit`; `explain=True` for a per-hit `score_breakdown`, including `type_boost` | ✅ Shipped (`explain` unreleased) |
| `memory_excerpt` | Read inside ONE memory: literal case-insensitive `query` scan returning offsets, line and surrounding context, and/or a `start`/`end` character slice. Composable; offsets stay absolute; `total_matches` is the true count even when `max_matches` caps the payload | ✅ Shipped (unreleased) |
| `memory_stats` | Health: counts, confidence, dormancy, hygiene, review sweep, the `claims` backlog (`sample` enumerates every open claim, contradicted first; `open_actionable` vs `open`; `unverified` counts refs the prose never resolved, deliberately outside the backlog; `review_limit` raises the cap), and a `graph` block (edges, degree, type mix, orphans + `orphan_sample`, over-spread-cap; `review_limit` raises that sample too) | ✅ Shipped |
| `memory_relate` | Build typed graph edges | ✅ Shipped |
| `memory_edges` | Enumerate edges with both endpoints resolved to titles, namespaces and degree; filter by namespace / type / memory, paged | ✅ Shipped (unreleased) |
| `memory_unrelate` | Repair edges: retype in place, reverse the direction (combinable with a retype), or remove — provenance preserved throughout, collision reports `merged`; single or batch of up to 100 reviewed ops; `dry_run` | ✅ Shipped (`reverse` unreleased) |
| `memory_consolidate` | merge / summarize / deduplicate + read-only near-dupe suggest scan | ✅ Shipped |
| `memory_export` / `memory_import` | Back up / transfer a namespace; import embeds what it writes and reports `embeddings_written` | ✅ Shipped (import embedding unreleased) |
| `memory_namespaces` | Namespace CRUD + `default_repo` (what a bare "PR #12" means here; `""` = not a repo) | ✅ Shipped |
| `credential_*` | OS-keychain secret vault | ✅ Shipped |
| `suggested_relations` hint | Surface candidates to examine for a _directional_ edge at store time; compact payload (title + ~200-char summary) | ✅ Shipped (v0.3.8; compacted v0.12.0; reframed from "link these" to "examine these" unreleased) |
| Write-time hints report an absolute `similarity` | Both hint lists are gated on cosine (or token Jaccard without embeddings) instead of the retrieval rank score, and report `similarity` + `basis` rather than `score`. An unrelated payload now returns an empty list instead of three candidates | ✅ Shipped (unreleased) |
| Relation discipline | Guidance ranks `supersedes`/`contradicts`/`caused_by`/`parent_of` first; `related_to` is a fallback, not a default | ✅ Shipped (unreleased) |
| Pinned memories | A per-namespace tier that always loads, exempt from ranking; additive to `limit`, capped at 20. Reported by every read path and preserved across an export/import round trip | ✅ Shipped (v0.15.0; correct reporting + round-trip unreleased) |
| Relation-graph metrics | `memory_stats.graph`: measures the orphan/low-signal/over-cap conditions that degrade retrieval | ✅ Shipped (unreleased) |
| Orphan enumeration | `graph.orphan_sample` + `memory_search(orphans=True)` name the memories the orphan count reports, costliest first — one shared predicate, so count and enumeration cannot drift | ✅ Shipped (unreleased) |
| Type-weighted spreading activation | Neighbour selection prefers directional relation types (`RELATION_WEIGHT`), ranked below confidence so a `supersedes` edge cannot promote the deprecated memory it points at | ✅ Shipped (unreleased) — real-brain spread budget 67.7% → 73.0% directional, retrieval metrics unchanged |
| User-level + repo protocol management | `gingugu init` installs/refreshes the protocol in a marked block in `~/.claude/CLAUDE.md` and, now, a repo's own `CLAUDE.md`/`AGENTS.md` (existing files only, never created); append-only outside the markers, refuses on an unmanaged protocol. `--adopt` wraps an existing hand-written section (matched by heading title) into the markers and refreshes it in one command. No `--global` flag, and nothing we ship may name one | ✅ Shipped (v0.14.0; repo files + `--adopt` unreleased) |
| `age` payload field | Derived-at-read relative age on every memory (full, compact, and write-time hints); never persisted | ✅ Shipped (v0.13.0; anchored on the freshness anchor + elaborated to `"7 weeks ago (updated just now)"` unreleased) |
| Freshness anchor is a MAX | `reference_timestamp` returns the latest of `last_confirmed`/`updated_at`/`created_at` instead of the first non-null, in Python and in SQL | ✅ Shipped (unreleased) |
| A rewrite is a confirmation | `memory_update` advances `last_confirmed` when title/content actually changed; retype/tag/metadata edits do not | ✅ Shipped (unreleased) |
| Memory Explorer UI | Browse graph + dashboard | ✅ Shipped |
| `gingugu ui` (launcher) | One command serves the built UI + live `/api/export` on one port (no Node); `--dev` for Vite hot reload. Bundle ships in the wheel | 🔧 Built (v0.9.0, pending release) |
| `gingugu serve` (transport) | Run over streamable HTTP + Bearer auth (hosted/central) | ✅ Shipped |
| `MEMORY_CREDENTIALS_ENABLED` flag | Run an instance without the credential vault | ✅ Shipped |
| `gingugu promote` (client) | Promote local gold → central brain (filter + provenance, idempotent) | ✅ Shipped (Stage 1) |
| `gingugu init` (bootstrap) | Install SessionStart+Stop hooks + `/sink-the-ship` (Claude Code) or a rules file (`--client`); non-destructive settings merge; `--force` copies anything it replaces to `<name>.bak` first, on every write path | ✅ Shipped (backup-on-force unreleased) |

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
