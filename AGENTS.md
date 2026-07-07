# AGENTS.md

Rules and conventions for AI agents and human contributors working in this repository.
Read this before making any changes.

---

## 🧠 Memory Protocol

Gingugu is your long-term brain. Memory is split into **two layers**:

1. **`crow`** — your global namespace. Identity, preferences, cross-project
   wisdom, meta-learnings, opinions about tools/tech. Loaded FIRST at every
   session start. (Named after the crow's nest — sees across all horizons.)
2. **Project namespace** — one per repo (e.g. `gingugu`, `my-app`). Schema
   decisions, bug history, release quirks, specific commits, file paths. Loaded
   AFTER crow.

### What goes where

- References a specific repo, file, commit, branch, or project decision →
  **project namespace**
- About HOW you think, work, communicate, or collaborate → **crow**
- Patterns that transcend any one codebase → **crow**
- Tool preferences, debugging instincts, opinions about tech → **crow**
- **When in doubt, project-scope it.** Crow is for things that genuinely apply
  to any project.

### Session start

**Load everything the workspace might need, in parallel. Don't ask the user
which repo they care about — the workspace itself is the answer.**

1. `memory_context(namespace="crow,<project>[,<project2>…]", task_hint=…)` - ONE
   call loads the identity foundation plus every repo in the workspace,
   de-duplicated across namespaces. Add `compact=true` for a lighter payload
   and pull full bodies with `memory_recall` when a memory matters.
2. `memory_stats(namespace="crow")` — global health pulse (dormancy is a
   resting signal, not rot; never auto-forgotten)
3. `memory_stats(namespace="<project>")` for each project namespace, in
   parallel with step 2

Multi-repo workspaces are common (e.g. `gingugu` + `gingugu.com` side-by-side).
Load them all speculatively — the cost is near-zero and it prevents an
unnecessary clarifying question before any real work starts.

If no project namespace exists yet for a repo in the workspace, create one:
`memory_namespaces(action="create", name="<project>")`. One namespace per
project keeps context clean.

### Working memory — daily protocol

- **Before non-trivial work:** `memory_recall` for the specific topic. Use
  `memory_search` when you need precision (filter by tags, date range, type,
  or confidence level).
- **When something changes:** `memory_update` the affected memory (e.g. mark a
  bug FIXED) instead of leaving stale records.
- **When something is wrong:** `memory_forget` it. Don't leave lies in the
  system — deprecate or hard-delete definitively incorrect memories.
- **Periodically:** run `memory_consolidate` on clusters of related memories
  (strategy: `merge` for duplicates, `summarize` for sprawl, `deduplicate`
  for exact repeats). A good time is session-end or when you notice 3+ memories
  on the same narrow topic.
- **Before destructive ops:** `memory_export` the namespace as a backup.
  Use `memory_import` to restore or transfer memory between environments.

### Questions — ALWAYS check memory before asking the user

Before asking the user ANY question — about a process, a decision, a config
value, a credential, a file path, a preference, or anything else — run
`memory_recall` or `memory_search` against the relevant namespace first.

If the answer is in memory: **use it, don't ask**.
If memory is empty or inconclusive: ask once, then immediately store the answer.

**Zero tolerance for asking something that was already answered in a prior session.**

### Credentials — ALWAYS check before asking the user

- `credential_list` — see what's vaulted (check this FIRST when a secret is
  needed, before asking the user to provide one)
- `credential_get` — retrieve a secret for use (e.g. the PyPI or npm token)
- `credential_store` — vault new secrets immediately, never leave them in
  files or chat history
- `credential_delete` — remove revoked/rotated credentials (then re-store the new one)

### Saving philosophy — treat Gingugu as live working memory

**Don't filter. Just save.** Gingugu has trust-led scoring, consolidation, and
dormancy tracking (never forgetting) — volume is its problem, not yours. Your
job is to be the input stream.

**Mental model:** a human expert working on this codebase all day doesn't decide
what to remember. They just work and their brain records continuously. Be that brain.

**Default: save. Immediately.** Don't batch saves for end of session. Save at the
moment of observation with `memory_store` (pass the appropriate namespace —
`crow` for identity/cross-project, the project namespace for repo-scoped).

Save whenever you read a file and understood it, ran a command and saw output,
hit an error (even one fixed immediately), noticed a pattern or convention, saw
a config/version/path that matters, made a trade-off, disproved an assumption,
or completed a task. For `crow`: when you formed an opinion, noticed how the
user works, reached a cross-project conclusion, or had a reflection worth keeping.

**The only reason not to save:** you stored the exact same thing 5 minutes ago.

Set `confidence="verified"` when proven by a test, run, or explicit confirmation.
Use `confidence="inferred"` for conclusions you drew. Use `memory_update` when
reality changes — don't let stale records linger.

**After every `memory_store`, immediately relate it.** Check the last few
memories surfaced by `memory_context`/`memory_recall` — if any connect to what
you just stored, call `memory_relate` right then. (`memory_store` also returns
`suggested_relations` — act on them.) Don't defer this; the graph only gets
useful if you build edges aggressively.

- `supersedes` — new memory replaces an older one (e.g. bug marked FIXED)
- `related_to` — two memories cover related topics (most common — use liberally)
- `caused_by` — one thing led to another
- `contradicts` — new info conflicts with old (then `memory_forget` the wrong one)
- `parent_of` / `child_of` — hierarchical grouping

**Rule of thumb:** if you store 3 memories in a session and create 0 relations,
you're doing it wrong. Most work is connected to prior work.

### What to remember (memory types)
- **architecture** — schema decisions, scoring/ranking changes, module boundaries
- **decision** — trade-offs made, rejected alternatives
- **bug** — issues found and fixes applied (update to FIXED when resolved)
- **pattern** — recurring design choices, idioms, approaches worth reusing
- **fact** — concrete state: versions, file locations, config values, test counts
- **preference** — your opinions, the user's working style, tool choices
- **workflow** — process steps, sequences, how something gets done
- **context** — background, reflections, milestones, the *why* behind the *what*

---

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
