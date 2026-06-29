# Project Status

_Last updated: 2026-06-29_

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

- **Networked brain (Phase 5 reframe → "The Crow's Nest").** Building toward a
  hosted/central gingugu instance fed by repo ingestion + selective local→central
  knowledge promotion. Transport keystone (`gingugu serve`) is done; the
  promotion bridge (external agent, with a provenance stamp) is the next build.
  See `docs/roadmap.md` and the architecture memory in the `gingugu` namespace.

## Blocked / Pending

- _None tracked._

## Known Issues

- _None tracked._

## Recently Completed

- **2026-06-29** — `gingugu serve` streamable-HTTP transport with Bearer-token
  auth and a `/healthz` probe; self-persisting token at `<db-dir>/serve_token`;
  `MEMORY_CREDENTIALS_ENABLED` flag to run an instance without the credential
  vault. New `serve.py` module; 9 tests (`tests/test_serve.py`), 185 total.
  Verified live (auth gating + full MCP handshake + client store/recall against
  a central instance over the wire). Branch `feature/serve-transport`.
- **2026-06-29** — Reconciled `docs/roadmap.md` with shipped reality (Phase 4 →
  Phase 5 complete / Phase 6 in flight; 112 → 176 test count; embeddings + RRF
  marked shipped).
- **2026-06-29** — Positive-path unit tests for `_suggest_relations`
  (`tests/test_suggest_relations.py`): mocked search scores pin threshold,
  self/exclude-id, already-related, and limit behavior.
- **2026-06-29** — README "Memory Explorer UI" section clarified: explicit
  Terminal 1 / Terminal 2 labels + Node.js 18+ prerequisite.
- **2026-06-29** — `handlers/memory.py` split (PR #7): read tools
  (`memory_recall`, `memory_context`) moved to new `handlers/recall.py`;
  `memory.py` keeps the write side. `memory.py` 327→203, `recall.py` 152.
  Shared helper imports repointed from `.memory` to `.helpers`.
- **2026-06-26** — Claude Code onboarding kit merged (PR #6); history scrubbed
  of work-repo references + Claude co-author lines (gingugu is public/personal).
- **2026-06-25** — Claude Code config + AI knowledge base added (this kit):
  generic `.claude/hooks/`, `settings.json`, `/creating-pr` (GitHub) +
  `/sink-the-ship` commands, `CLAUDE.md`, `AGENTS.md`, populated `.ai/`, and
  `.gitignore` additions (`logs/`, `.claude/data/`, hook `__pycache__`).
- **2026-06-24** — v0.3.8: `suggested_relations` hint on `memory_store` /
  `memory_update`; 2 contract tests; released to PyPI.

## Next Up

- **Promotion bridge** — external agent that reads promotable memories from a
  local brain (verified bug/pattern/decision/architecture/fact, minus personal
  or episodic noise) and writes them to central with a provenance stamp.
- Repo-ingestion agent to cold-seed central with org breadth.
- Data-ownership decision before hosting work-repo knowledge (personal vs
  company AWS, or scrubbed/synthetic seed).
- Phase 6 backlog (hybrid RRF retrieval, structured provenance) — see `docs/roadmap.md`.
