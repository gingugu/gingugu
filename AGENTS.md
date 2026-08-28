# AGENTS.md

Rules and conventions for AI agents and human contributors working in this repository.
Read this before making any changes.

---

<!-- BEGIN GINGUGU MEMORY PROTOCOL -->
<!-- Managed by `gingugu init`. Edits between these markers are
     replaced on re-run; put your own rules outside them. -->

## Memory Protocol

Gingugu is your long-term brain. Memory is split into **two layers**:

1. **`crow`** — your global namespace. Identity, preferences, cross-project
   wisdom, opinions, meta-learnings. Loaded FIRST every session.
2. **Project namespace** (named for this repo) — schema decisions, bug history,
   deploy quirks, specific commits. Loaded AFTER crow.

**What goes where:**
- References a specific repo, file, commit, or project decision → project
- About HOW you think, work, or collaborate → `crow`
- Patterns/opinions that transcend any one codebase → `crow`
- When in doubt, project-scope it.

### Session start
1. `memory_context(namespace="crow,<project>[,<project2>…]", task_hint=…)` — one
   call loads the identity foundation plus every repo in the workspace,
   de-duplicated across namespaces. Load them all speculatively rather than
   asking which one to focus on. Add `compact=true` for a lighter payload and
   pull full bodies with `memory_recall` as needed.
2. `memory_stats(namespace="crow")` — global health.
3. `memory_stats(namespace="<project>")` for each project namespace.

If a repo has no project namespace yet, create it:
`memory_namespaces(action="create", name="<project>")`.

### During the session
**Default: save. Immediately.** Don't filter, and don't batch saves for the end
of a session — save at the moment of observation, because that is the moment the
detail still exists. Save whenever you:

- read a file and understood what it does
- ran a command and saw its output
- hit an error, even one you fixed immediately
- made any trade-off, or rejected an alternative
- completed a task
- formed an opinion about a tool or approach
- noticed something about how the user works or decides

Project namespace for anything naming a repo, file, commit, or decision; `crow`
for opinions, working style, and conclusions that outlive this one project.

**Before asking the user any question** — run `memory_recall` or `memory_search`
first. If the answer is in memory, use it. Don't ask the same thing twice.

Use `memory_update` when something changes. Set `confidence="verified"` when
proven; `inferred` for conclusions. When something turns out to be **wrong**, use
`memory_forget` — deprecate it, or hard-delete it if it was never true. A
confidently wrong memory costs more than a missing one.

**Relating memories — quality, not volume.** An edge is worth writing only when
it records something search cannot infer. Recall already ranks by hybrid text +
semantic similarity, so "same topic" is knowledge the index has for free. Use
`memory_relate` for direction and time, preferring in this order: `supersedes`
(this replaces that), `contradicts`, `caused_by`, `parent_of`/`child_of`. Treat
`related_to` as a fallback for a real connection none of those describe — never
as shorthand for "similar". Spreading activation surfaces at most 3 neighbours
per memory and ranks them by relation type, so a vague edge loses its slot to a
directional one and buys you nothing. Precise edges still compete with each
other for those 3 slots. If you can't name the directional fact an edge records,
don't create it.

### Credentials
Gingugu carries an OS-keychain vault, so secrets never belong in files or chat.

- **`credential_list` FIRST**, before asking the user for any secret, token, or
  API key — it may already be vaulted.
- `credential_get` to retrieve one for use.
- `credential_store` to vault a new one the moment you receive it.
- `credential_delete` when one is revoked, then store the replacement.

### Memory types
`fact`, `decision`, `architecture`, `bug`, `pattern`, `workflow`, `context`,
`preference`.

<!-- END GINGUGU MEMORY PROTOCOL -->
## What This Repo Is

**gingugu** is a local **MCP server** that gives AI coding assistants persistent,
structured, searchable long-term memory across sessions, repos, and projects.
Everything is local: one SQLite file on the user's machine, no cloud, no API
keys, no telemetry. Published to PyPI as `gingugu`; the repo is **public** on
GitHub (`gingugu/gingugu`). A React/Vite "Memory Explorer" UI lives under `ui/`.

This is the product itself — **dogfood it**. Use the memory protocol above in
your own session, on the `gingugu` namespace.

---

## Stack

- **Python** `>=3.11` (CI matrix: ubuntu/macos/windows × 3.11–3.13)
- **MCP** Python SDK (`mcp>=1.25,<2`), stdio transport
- **SQLite** + **FTS5** for full-text; semantic embeddings for hybrid retrieval
- **platformdirs** for the cross-platform DB path
- **uv**-managed; `ruff` + `black` formatting; `pytest` + `pytest-asyncio`
- **UI:** Node/React/Vite + Tailwind under `ui/` (FastAPI-style `ui/api.py` backend)
- Released via **Trusted Publishing (OIDC)** to PyPI on tag

---

## Repo Map

```
src/gingugu/
  server.py          → MCP server entrypoint; registers tools, never crashes
  config.py          → config + cross-platform DB path (platformdirs)
  database.py        → SQLite connection, schema, WAL, migrations (PRAGMA user_version), FTS5 triggers
  models.py          → memory/namespace/relation data models
  storage.py         → memory CRUD (store/update/forget)
  search.py          → hybrid BM25 (FTS5) + semantic ranking
  embeddings.py      → semantic vector generation
  context.py         → session priming (memory_context) + spreading activation
  relations.py       → graph edges between memories
  consolidation.py   → merge / summarize / deduplicate clusters
  decay.py           → dormancy as a resting signal (NEVER auto-forgets)
  stats.py           → health stats (counts, confidence, dormancy, hygiene)
  namespaces.py      → namespace CRUD
  credentials.py     → OS-keychain credential vault
  portability.py     → export / import a namespace
  handlers/          → MCP tool handlers: memory.py, search.py, relations.py,
                       admin.py, credentials.py, helpers.py
ui/                  → Memory Explorer (api.py backend + React/Vite frontend)
docs/                → architecture.md (mermaids = source of truth), roadmap.md, future-architecture.md
tests/               → pytest suites (unit + integration MCP flows)
```

---

## Non-Negotiable Conventions

### The server must never crash

Every MCP tool handler wraps its body in try/except and returns a structured
result (`{"ok": false, "error": ...}` on failure). A handler that raises out of
the server takes down the user's whole memory layer. Telemetry/logging failures
must be non-fatal.

### Storage discipline

- Schema changes are **migrations** keyed off `PRAGMA user_version` — additive
  by default, never destructive without explicit user approval.
- **WAL mode always** (`PRAGMA journal_mode=WAL`) for concurrent reads.
- **FTS5 sync triggers** stay in lockstep with any change to the `memories`
  table — a schema change that skips the triggers silently breaks search.
- Back up the DB file before any consolidation/prune touching >100 rows.

### Never forget

Dormancy is a *resting signal*, not deletion. Memories are never auto-forgotten;
only explicit `memory_forget` removes anything. Do not reintroduce time-based decay.

### Tests track the surface

No PR without tests for the changed surface. Storage, search, relations, and
context changes need unit coverage; tool changes need an integration flow
(store → recall → context). `pytest-asyncio` — handlers are async.

### Keep files small

Max 300 lines per module; split early. `handlers/memory.py` is the current
watch item (see `.ai/plans/status.md`).

---

## Documentation Standards

- **README** — keep in sync with `pyproject.toml`, the MCP tool surface, and
  setup/config. Use absolute `https://github.com/gingugu/gingugu/blob/main/...`
  URLs for any file/asset reference (relative links 404 on PyPI).
- **CHANGELOG.md** — Keep a Changelog format; add an `[Unreleased]` entry for
  every user-visible change.
- **docs/architecture.md** — the mermaid diagrams are the source of truth for
  system design; update them when modules or flows change.
- **Knowledge base** — keep `.ai/` current (see below) on every commit/PR.

---

## Quality Assurance

- Define clear acceptance criteria before starting work
- `uv run pytest -v` green before opening a PR
- `ruff` + `black` clean
- Surface performance characteristics (DB size, query latency) when relevant
- Surface uncertainties — never guess

## Collaboration

- **Code reviews** — mandatory for all changes (GitHub PR)
- **Design decisions** — document rationale and trade-offs (in `.ai/specs/` and Gingugu)
- **Communication** — clear, timely, actionable; flag blockers early

---

## AI Knowledge Base (.ai/)

The `.ai/` folder is a living knowledge base. **AI agents must assess and update it before every commit or PR.**

| File | Update when |
|------|-------------|
| `.ai/plans/status.md` | Always — current in-progress, blocked, and recently completed work |
| `.ai/memory.md` | Module structure, tool surface, storage schema, or release state changed |
| `.ai/specs/01-architecture.md` | New module/tool added, storage model changed, or a key decision was made |
| `.ai/specs/dataflow.md` | The store/embed/recall/context flow, relations, or spreading activation changed |
| `.ai/specs/product-spec.md` | A tool/feature shipped, got blocked, or was descoped |
| `.ai/agents/` | Tech stack decision, directory structure, or agent rule changed |
| `.ai/standards/` | Testing, code, or database discipline changed |

When creating a PR, always use the `/creating-pr` command
(`.claude/commands/creating-pr.md`). It includes the mandatory `.ai/` assessment.

---

## Git Conventions

GitHub repo — use `gh`, not `glab`.

- **Branches:** `feature/`, `bugfix/`, `fix/`, `hotfix/`, `docs/`
- **Commits:** `<type>: <what changed> - <why it changed>` (types: `feat`, `fix`, `docs`, `chore`, `refactor`)
- **Never commit:** secrets, credentials, `*.db`, `.venv/`, `__pycache__/`, `node_modules/`, build output

---

## Repo Structure Reference

See `README.md` for the full repository structure, feature list, MCP client setup, and quick start.
