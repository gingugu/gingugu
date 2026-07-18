# Standards: Code & Testing

## Code

- **Python `>=3.11`**, PEP 8, type hints required on all public functions.
- **`ruff` + `black`** clean before every commit (`uv run ruff check . && uv run black .`).
- **300-line file limit** per module — split early into helpers/submodules. One
  responsibility per file.
- **Simplicity over cleverness** — no premature abstraction.
- **Pin dependencies** in `pyproject.toml`; verify against official docs (MCP
  spec, SQLite FTS5, `mcp` SDK) before adding or upgrading.

## Error handling — the server must never crash

- Every MCP tool handler wraps its body in try/except and returns a structured
  result: `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.
- No exception escapes `server.py` to the client. A crash takes down the user's
  entire memory layer.
- Telemetry, logging, and dedupe/relation hint computation are **non-fatal** —
  a failure there must not fail the underlying operation.

## Testing

- **`pytest` + `pytest-asyncio`** — MCP handlers are async.
- **No PR without tests** for the changed surface.
- **Unit tests** for storage, search, relations, context, decay, consolidation.
- **Integration tests** for end-to-end MCP flows (store → recall → context;
  store → relate → recall include_related).
- Run `uv run pytest -v` green before opening a PR.
- CI matrix: ubuntu/macos/windows × Python 3.11–3.13 — cross-platform claims must
  be backed by green CI on all three OSes, not just local.
- **Ranking/scoring changes ship with benchmark evidence:** run
  `uv run python -m bench` (fixture floor) and a real-brain run against the
  recorded baseline (see `docs/roadmap.md` Phase 5.75). Grading is
  deterministic math only — never LLM-as-judge (design law, 2026-07-18).

## Docs in lockstep

- Update `CHANGELOG.md` (`[Unreleased]`) for every user-visible change.
- Keep `README.md` and `docs/architecture.md` mermaids in sync with the tool surface.
- Update `.ai/` per the enforcement table before every commit/PR.
