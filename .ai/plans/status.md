# Project Status

_Last updated: 2026-06-25_

## Shipped / Working

- **v0.3.8 on PyPI** — released via Trusted Publishing (OIDC) on tag; GitHub
  Release auto-cut from CHANGELOG.
- **Two-layer memory** — `crow` (global identity) + per-project namespaces, live.
- **Never-forget model** — dormancy + spreading activation replaced time-based
  decay; nothing is auto-forgotten.
- **Hybrid retrieval** — BM25 (FTS5) + semantic ranking on `memory_recall`.
- **suggested_relations + similar_memories** — non-blocking hints on
  `memory_store` / `memory_update` (link vs merge candidates).
- **Credential vault** — OS-keychain backed; `credential_*` tools.
- **Memory Explorer UI** — React/Vite graph + dashboard under `ui/`.
- **Cross-platform** — platformdirs DB path; CI green on ubuntu/macos/windows × 3.11–3.13.

## In Progress

- **Claude Code onboarding kit** — PR #6 (`feature/claude-code-onboarding`).
  CI was red on all 9 matrix jobs: `ruff check .` / `black --check .` graded the
  ported `.claude/hooks/` kit against gingugu's style. Fixed by excluding
  `.claude/` from both (it's portable agent tooling, not the package). Awaiting
  re-run + merge.

## Blocked / Pending

- _None tracked._

## Known Issues

- **`handlers/memory.py` exceeds the 300-line limit** — needs a refactor in its
  own PR (suggested split: per-tool modules, or `read.py` + `write.py`).

## Recently Completed

- **2026-06-25** — Claude Code config + AI knowledge base added (this kit):
  generic `.claude/hooks/`, `settings.json`, `/creating-pr` (GitHub) +
  `/sink-the-ship` commands, `CLAUDE.md`, `AGENTS.md`, populated `.ai/`, and
  `.gitignore` additions (`logs/`, `.claude/data/`, hook `__pycache__`).
- **2026-06-24** — v0.3.8: `suggested_relations` hint on `memory_store` /
  `memory_update`; 2 contract tests; released to PyPI.

## Next Up

- Refactor `handlers/memory.py` under 300 lines (own PR).
- README "Memory Explorer UI" section: clearer Terminal 1 / Terminal 2 labels + Node.js prereq.
- Positive-path unit test for `_suggest_relations` with mocked search scores.
- Phase 6 backlog (hybrid RRF retrieval, structured provenance) — see `docs/roadmap.md`.
