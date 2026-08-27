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
per memory and does not weight by type, so a vague edge crowds out a useful one.
If you can't name the directional fact an edge records, don't create it.

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

## Available Commands

- `/creating-pr` - Create a PR with mandatory `.ai/` knowledge base assessment
- `/sink-the-ship` - Save everything to Gingugu and end session

## Conventions

- **MCP tools:** every tool handler returns a structured dict (`ok`/error), never raises out of the server
- **Storage:** schema changes are migrations keyed off `PRAGMA user_version`; FTS5 sync triggers stay in lockstep with the `memories` table; WAL mode always
- **Tests:** no PR without tests for the changed surface; `pytest` + `pytest-asyncio` (handlers are async)
- **Docs:** keep `docs/architecture.md` mermaids + `CHANGELOG.md` (Keep a Changelog) in sync with the tool surface
