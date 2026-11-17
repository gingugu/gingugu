# gingugu

> **Primary context:** Read `AGENTS.md` for full conventions, module map, memory protocol, and non-negotiable rules.

## Quick Reference

- **Install (dev):** `uv sync`
- **Run the server:** `uv run gingugu` (MCP stdio transport)
- **Run tests:** `uv run pytest -n auto` (parallel; `-v` for detail)
- **UI tests:** `cd ui && npm test` (also `npm run typecheck`)
- **Format + lint:** `uv run ruff check . && uv run black .`
- **Memory Explorer UI:** backend `uv run python ui/api.py`; frontend `cd ui && npm install && npm run dev`

## What This Repo Is

Gingugu is a local **MCP server** that gives AI coding assistants persistent,
structured, searchable long-term memory across sessions, repos, and projects.
Pure Python, one SQLite file, no cloud, no API keys, no telemetry. Published to
PyPI (`gingugu`); public repo on GitHub (`gingugu/gingugu`). A React/Vite
"Memory Explorer" UI ships alongside under `ui/`.

## Critical Rules

1. **No automatic changes** - require explicit approval before any change
2. **Never commit secrets, credentials, or API keys**
3. **Verify all technical claims** against official docs before acting (MCP spec, SQLite FTS5, `mcp` Python SDK)
4. **300-line file limit** - refactor into modules/helpers if exceeded
5. **Never guess or assume** - research and verify before proceeding

## Reasoning & Agentic Operation (Opus 4.8)

Reasoning happens internally - do not narrate it. Surface conclusions, not deliberation.

- **Surface in output:** decision rationale (concise), identified risks/blockers, approval requests with explicit scope of change.
- **Do not surface:** confidence percentages, step-by-step reasoning walkthroughs, redundant caveats and hedging.
- **Reason hardest for:** schema or scoring-algorithm changes, MCP tool-surface (breaking API) changes, retrieval/ranking logic, and any change touching >3 files or >100 lines.
- **Tool use:** batch independent tool calls in parallel; go sequential only when one call's input genuinely depends on another's output.

## AI Knowledge Base Enforcement

This repo maintains a living knowledge base in `.ai/`. These rules apply to every session and cannot be skipped.

### Before every commit or PR - mandatory assessment

| File | Update when |
|------|-------------|
| `.ai/plans/status.md` | **Always** - reflect current in-progress, blocked, and recently completed work |
| `.ai/memory.md` | Module structure, tool surface, storage schema, or release state changed |
| `.ai/specs/01-architecture.md` | New module/tool added, storage model changed, or a key decision was made |
| `.ai/specs/dataflow.md` | The store -> embed -> recall -> context retrieval flow or relations/spreading-activation changed |
| `.ai/specs/product-spec.md` | A tool/feature shipped, got blocked, or was descoped |
| `.claude/agents/` | A subagent's scope, tool allowlist, or model tier changed |
| `.ai/standards/` | Testing, code, or database discipline changed |
| **gingugu.com** (`~/GIT/gingugu.com`) | `README.md`, `CHANGELOG.md`, `docs/` or `product-spec.md` changed - the site must say the same thing; open its PR in the same session (and the reverse: a site change means checking `README.md`) |

### PR creation

Always use the `/creating-pr` skill (`.claude/skills/creating-pr/SKILL.md`) when opening a PR.
Never skip the `.ai/` assessment. Never open a PR without updating `status.md`.

## Key Specs

- `.ai/specs/01-architecture.md` - System architecture and module map
- `.ai/specs/product-spec.md` - MCP tool surface and feature coverage
- `.ai/specs/dataflow.md` - Store/recall/context retrieval, relations, spreading activation

## Git Workflow

GitHub repo - use `gh`, not `glab`.

- **Branches:** `feature/[name]`, `bugfix/[name]`, `fix/[name]`, `hotfix/[name]`, `docs/[name]`
- **Commits:** `<type>: <what changed>` (feat, fix, docs, chore, refactor) - include what + why + impact
- **PRs:** descriptive titles and bodies; use the `/creating-pr` skill
- **Multiple PRs in one session: stack them.** Cut the second branch from the first and `gh pr create --base <first-branch>`. Every PR must add a `.ai/plans/status.md` entry at the same anchor, so two branches off `main` conflict on the second merge, every time. See step 0 of `/creating-pr`.
- **Merging a stack:** never `--delete-branch` the parent - it closes child PRs and GitHub will not reopen them. Retarget children to `main` first.
- Run `git status` before every commit
- Never commit `*.db`, `.venv/`, `__pycache__/`, `.DS_Store`, `node_modules/`

## Code Quality

- **Approval-first** - no changes without explicit sign-off
- **Simplicity** - simple over clever; avoid premature abstraction
- **Style** - PEP 8, type hints on all public functions, `ruff` + `black` clean
- **Error handling** - the MCP server must **never crash**; tool handlers wrap in try/except and return structured error responses
- **File limit** - max 300 lines per module; split early
- **Dependencies** - pin in `pyproject.toml`; verify against official docs before adding

## Security

- Never commit secrets, credentials, or API keys
- The memory DB lives at the platform data dir (e.g. `~/.local/share/gingugu/memories.db`) - never inside the repo
- Credentials vault uses the OS keychain - never write secret values to files, logs, or chat
- This is a **public** repo - never embed internal URLs, tokens, customer names, or PII in docs, code, or examples

## Verification Standards

Before any change:

1. Verify against official documentation (MCP spec, SQLite FTS5, `mcp` SDK versions, breaking changes)
2. Surface uncertainties - research before proceeding, never guess
3. Request explicit approval - explain what changes, why, and highlight risks

Before any write to external systems (GitHub API, PyPI, npm):

- Present the exact command or API call
- Explain what it changes and blast radius
- Wait for explicit approval - zero exceptions

## Available Skills

- `/creating-pr` - Create a PR with mandatory `.ai/` knowledge base assessment and a mandatory `ai-docs-auditor` run whose confirmed findings are fixed in the same PR
- `/releasing` - Cut a release (`scripts/release.py`) and roll it out to every machine (`scripts/fleet.py`)
- `/sink-the-ship` - Save everything to Gingugu and end session

## Available Subagents (`.claude/agents/`)

Tier rule: Opus is never a minion. None of the five has `Bash`; all have and `disallowedTools: Agent, mcp__gingugu`. Each runs its own fenced `brain` server (inline `mcpServers`, `MEMORY_GRANT: gingugu=read,minions=write`): read the brain, write only the `minions` scratch namespace.

| Agent | Tier | Use for |
|---|---|---|
| `ai-docs-auditor` | sonnet | Audits `.ai/` against the code, reports drift as `path:line`. Run before every PR. |
| `security-reviewer` | sonnet | Secrets, PII, vault boundary, path safety, destructive writes without a backup. Run before every commit touching credentials, bootstrap, or path handling. |
| `repo-scout` | haiku | Mechanical search and inventory: every place X appears, file lists, counts. Returns raw matches, never an interpretation. Shipped by `gingugu harness`; overrides a user-level `~/.claude/agents/repo-scout.md` with a repo-fenced grant. |
| `researcher` | sonnet | Read-only research across the repo and the public web, findings with sources. The fenced stand-in for `general-purpose`, which carries the Agent tool - never use that for minion work (`gingugu hook tool` denies a minion's spawn anyway). No shell: `gh`/`git` research stays on the main thread. Shipped by `gingugu harness`. |
| `schema-ladder-scout` | haiku | Inventories the `user_version` migration ladder and the FTS5 trigger columns; reports gaps, duplicates, and column mismatches. Run on any change under `migrations/` or to `memories_fts`. |

Not defined on purpose: there is no `backend` or `planner` agent. Both describe
Opus-tier work (schema, scoring, retrieval, sequencing), and that stays on the
main thread.

**Forks are the parallel path for that work.** `subagent_type: "fork"` (or
`/subtask`) inherits the whole conversation and model - a branch of the main
thread, so `gingugu hook tool` gives it the main thread's rights (it shows as
`agent_type: "fork"`). Use `isolation: "worktree"` when it writes. Forks design
and write the tests; the main thread approves; a Sonnet minion implements.

**A brand-new `.claude/agents/` directory is picked up mid-session, but on a
delay of minutes** - unlike `.claude/skills/`, which surfaces in seconds. The
first spawn after creating one will fail with "agent type not found"; that
measures latency, not impossibility. Write the definition, validate its
frontmatter structurally, get on with other work, and retry before the session
ends. Do not promise a live demo in the same breath as writing the file, and do
not declare it unusable until a restart either.

## Conventions

- **MCP tools:** every tool handler returns a structured dict (`ok`/error), never raises out of the server
- **Storage:** schema changes are migrations keyed off `PRAGMA user_version`; FTS5 sync triggers stay in lockstep with the `memories` table; WAL mode always
- **Tests:** no PR without tests for the changed surface; `pytest` + `pytest-asyncio` (handlers are async)
- **Docs:** keep `docs/architecture.md` mermaids + `CHANGELOG.md` (Keep a Changelog) in sync with the tool surface
