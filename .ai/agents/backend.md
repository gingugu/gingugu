# Agent: Backend (Python / MCP / Storage)

## Scope

The Python MCP server and its core: `server.py`, `handlers/`, `storage.py`,
`search.py`, `embeddings.py`, `context.py`, `relations.py`, `consolidation.py`,
`decay.py`, `stats.py`, `namespaces.py`, `credentials.py`, `portability.py`,
`database.py`, `config.py`, `models.py`.

## Rules

- **The server never crashes.** Every tool handler returns a structured dict;
  no exception escapes `server.py`. Telemetry/hint failures are non-fatal.
- **Tool contracts are an API.** Changing a tool's name, params, or return shape
  is a breaking change — reason hard, update `README.md` + `docs/architecture.md`
  + `CHANGELOG.md`, and add/adjust integration tests.
- **Storage discipline** per `.ai/standards/02-database.md`: WAL, `user_version`
  migrations (additive), FTS5 triggers in lockstep, never-forget.
- **300-line limit.** Split modules before they cross it — `handlers/` is
  already domain-split (write `memory.py`, read `recall.py`, `search.py`,
  `relations.py`, `admin.py`, `credentials.py`, shared `helpers.py`).
- **Async.** Handlers are async; test with `pytest-asyncio`.
- Verify against the MCP spec, SQLite FTS5 docs, and the `mcp` SDK before
  changing transport or schema behavior.

## Definition of done

- Tests green for the changed surface (unit + an integration flow).
- `ruff` + `black` clean.
- Tool docstrings, README, and `.ai/` updated to match.
