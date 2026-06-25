# gingugu

> **Primary context:** Read `AGENTS.md` for full conventions, module map, memory protocol, and non-negotiable rules.

## Quick Reference

- **Install (dev):** `uv sync`
- **Run the server:** `uv run gingugu` (MCP stdio transport)
- **Run tests:** `uv run pytest -v`
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

## Memory Protocol (Gingugu)

> The imperative startup sequence is injected every session by `.claude/hooks/session_start.py` (AGENTS.md is not auto-loaded into context, so the hook is the source of truth at startup).

This repo *is* Gingugu - dogfood it. See `AGENTS.md` for the full protocol. Key rules:

- Load `crow` namespace + all project namespaces at session start (in parallel)
- Check `memory_recall` before asking any question the user has already answered
- Check `credential_list` before asking for any secret or API token
- Save continuously - do not batch saves for end of session
- Relate memories aggressively with `memory_relate` after every store

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
| `.ai/agents/` | Tech stack decision, directory structure, or agent rule changed |
| `.ai/standards/` | Testing, code, or database discipline changed |

### PR creation

Always use the `/creating-pr` command (`.claude/commands/creating-pr.md`) when opening a PR.
Never skip the `.ai/` assessment. Never open a PR without updating `status.md`.

## Key Specs

- `.ai/specs/01-architecture.md` - System architecture and module map
- `.ai/specs/product-spec.md` - MCP tool surface and feature coverage
- `.ai/specs/dataflow.md` - Store/recall/context retrieval, relations, spreading activation

## Git Workflow

GitHub repo - use `gh`, not `glab`.

- **Branches:** `feature/[name]`, `bugfix/[name]`, `fix/[name]`, `hotfix/[name]`, `docs/[name]`
- **Commits:** `<type>: <what changed>` (feat, fix, docs, chore, refactor) - include what + why + impact
- **PRs:** descriptive titles and bodies; use `/creating-pr` command
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

## Available Commands

- `/creating-pr` - Create a PR with mandatory `.ai/` knowledge base assessment
- `/sink-the-ship` - Save everything to Gingugu and end session

## Conventions

- **MCP tools:** every tool handler returns a structured dict (`ok`/error), never raises out of the server
- **Storage:** schema changes are migrations keyed off `PRAGMA user_version`; FTS5 sync triggers stay in lockstep with the `memories` table; WAL mode always
- **Tests:** no PR without tests for the changed surface; `pytest` + `pytest-asyncio` (handlers are async)
- **Docs:** keep `docs/architecture.md` mermaids + `CHANGELOG.md` (Keep a Changelog) in sync with the tool surface
