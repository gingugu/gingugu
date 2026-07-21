# Project Status

_Last updated: 2026-07-20_

## Shipped / Working

- **v0.7.0 on PyPI** (2026-07-18) — released via Trusted Publishing (OIDC) on
  tag; GitHub Release auto-cut from CHANGELOG. Latest: benchmark toolset,
  true hybrid retrieval (independent BM25 + semantic pools, RRF-fused),
  hub-dampened relation traversal.
- **Two-layer memory** — `crow` (global identity) + per-project namespaces, live.
- **Never-forget model** — dormancy + spreading activation replaced time-based
  decay; nothing is auto-forgotten.
- **Hybrid retrieval** — BM25 (FTS5) + semantic ranking on `memory_recall`.
- **suggested_relations + similar_memories** — non-blocking hints on
  `memory_store` / `memory_update` (link vs merge candidates).
- **Credential vault** — OS-keychain backed; `credential_*` tools.
- **Memory Explorer UI** — React/Vite graph + dashboard under `ui/`.
- **Cross-platform** — platformdirs DB path; CI green on ubuntu/macos/windows × 3.11–3.13.
- **`gingugu init` bootstrap** — one command installs the Claude Code hook kit
  (SessionStart contract auto-inject + Stop save-discipline + `/sink-the-ship`),
  non-destructive `.claude/settings.json` merge; `--client` writes a rules file
  for Windsurf/Cursor/Cline. Closes the "our install beats the shipped install" gap.

## In Progress

- **Known retrieval gap (not yet addressed):** a memory at BM25 rank 1 AND
  semantic rank 1 can lose the composite top spots to high-`access_count`
  neighbours because RRF rank compression flattens relevance deltas
  (~0.7%/rank) below the access weight's reach (~3%). Reproduced empirically
  2026-07-20. Any fix goes through `bench/` first (benchmark-before-tuning).
- **Design-law reconciliation pending:** Phase 5.5 Stage 3's "local LLM
  judge" for conflict detection needs reconciling with the design law
  (truth status hard-calculated, never LLM-derived) — advisory-only
  proposing may comply, needs a call before Stage 3 is built.
- **Parked until data argues for it:** any temporal/entity graph work
  (2026-07-18 decision — benchmark-before-graph).
- **v0.4.0 released** (2026-07-07): serve, promote Stage 1, multi-namespace
  context + compact, review hints, suggest mode, save hook, timeline chart
  fix. Remaining: dogfood the new context loading after client restart; the
  `.claude/hooks/session_start.py` startup contract + global agent rules now
  reference the single multi-namespace `memory_context` call.
- **Networked brain (Phase 5 reframe → "The Crow's Nest").** Done: transport
  keystone (`gingugu serve`) and the promotion bridge **Stage 1**
  (`gingugu promote`, merged in PR #11). Next: **Stage 2** consolidation
  (merge near-dupes into one canonical memory with a `contributors[]` list),
  then **Stage 3** conflict detection (`contradicts` edges via a small local
  LLM judge / Ollama), then **Stage 4** wiring the source to the real local
  brain. See `docs/roadmap.md` and the architecture memory in the `gingugu`
  namespace.

## Blocked / Pending

- _None tracked._

## Known Issues

- _None tracked._

## Recently Completed

- **2026-07-20** - **v0.8.1: CLI front door.** `gingugu` now answers
  `-h`/`--help`/`help` (usage) and `-V`/`--version`/`version` (version), and an
  unknown subcommand errors to stderr with exit `2` instead of silently booting
  the stdio server and blocking on stdin. Bare `gingugu` and the
  `serve`/`promote`/`init` subcommands are unchanged. `tests/test_cli.py` (11
  cases) covers every dispatch path. No MCP tool-surface change.
- **2026-07-20** - **v0.8.0 released; review-sweep workflow merged (PR #25).**
  `memory_search` gained `ids` (precise fetch-by-ID: requested order,
  deprecated included, `missing` reported), `memory_stats` gained
  `review_limit` (enumerate all flagged memories, max 100), and gated review
  hints skip timeless types (`pattern`/`preference`) - eliminated the
  observed false positives on a real corpus. Sweep flow:
  `memory_stats(review_limit=100)` -> `memory_search(ids=...)` ->
  `memory_update`/`memory_forget`. 300 tests; benchmarked code-vs-code on a
  frozen corpus: zero retrieval delta.

- **2026-07-18** - **Phase 5.75 "The Sextant" complete** (PRs #22, #23 +
  hub-dampening PR). Retrieval quality is now a measured number: (1)
  dev-only `bench/` golden-set benchmark toolset (Recall@K, MRR, precision,
  token cost; deterministic, no LLM-as-judge; synthetic CI fixture +
  read-only real-brain mode with gitignored `bench/local/` golden sets);
  (2) recorded real-brain baseline — recall@5 = 1.000 on all 30 questions
  in both modes, rank-1 identified as the target (hybrid MRR 0.811 /
  recall@1 0.578); (3) true hybrid retrieval — independent BM25 + semantic
  pools, RRF over the union, entrants gated by a benchmark-tuned 0.55
  cosine floor (≤ limit/2), BM25 candidates never displaced: MRR → 0.828,
  recall@1 → 0.611, recall@10 held 1.000 (accepted trade: one multi
  question's secondary hit at rank 6–10); (4) hub-dampened relation
  traversal — `include_related` extras + spreading activation share one
  budgeted neighbourhood (≤3/seed by confidence → low degree → recency,
  ≤10 total): mean extras 18.9 → 9.9, extra payload ~9.4k → ~4.8k tokens.
  `search.py` split into engine + `search_common` + `search_filters`.
- **2026-07-08** - Multi-namespace `memory_recall`/`memory_search`: `namespace`
  accepts a CSV list, searched in one ranked SQL pass (`limit` = total cap,
  unlike context's per-namespace limit); multi responses carry `namespaces[]`;
  recall/search results now stamp each memory's home namespace like context.
  Comma-hint errors on single-namespace tools + `memory_store` junk-namespace
  guard. Root cause: observed an agent generalize context's CSV form to recall
  and hit `namespace 'a,b' not found`. Same PR: `compact` mode on
  recall/search (context's 0.4.0 payload diet; related extras compacted too) -
  fixes broad recalls blowing MCP clients' tool-result token caps (Claude
  Code was dumping 80k+-char recall results to files). 14 new tests, 269 total.
- **2026-07-07** - Feedback arc peer-reviewed and MERGED (PRs #12, #15, #14;
  main @ 47ea06e). 8-finder/6-verifier review confirmed 21 findings; all
  fixed in 1e05867 (staleness regex hardening, empty-namespace guard,
  suggest-gate tightening, modal-dim embedding filter, stats prefilter,
  hook state-root + write-tool set, threshold 0.85 → 0.9). 237 tests.
  Real-brain DESI-54 dupe pair consolidated (backup taken first).
- **2026-07-07** - Save discipline + dupe surfacing (PR C of the feedback
  arc): `memory_consolidate` suggest mode (read-only pairwise-embedding
  near-dupe scan, title-only fallback, 1000-memory cap) and a
  `--check-memory-saves` flag on the `.claude` kit Stop hook (blocks a stop
  once per session when ≥15 tool calls but zero gingugu writes - guards the
  lost-session failure mode). 8 new tests, 228 total.
- **2026-07-07** - Staleness review hints (PR B of the feedback arc): new
  `staleness.py` detector for point-in-time content (open-PR references,
  waiting-on phrasing, unmerged branches - gated on 14 days unconfirmed;
  expired/as-of dates fire immediately). Advisory `review_hints` on
  `memory_context` results + `review` block in `memory_stats`. Never mutates.
  14 new tests, 220 total.
- **2026-07-07** - Context efficiency (PR A of the feedback arc):
  `memory_context` accepts a comma-separated namespace list and de-dupes
  across loads (cross-namespace patterns previously repeated per namespace);
  new `compact` mode returns title + ~200-char excerpt; context loads now
  refresh the dormancy clock only instead of bumping `access_count` (closes
  the rich-get-richer ranking loop). 5 new tests, 206 total.
- **2026-07-07** - PR #11 merged: promotion bridge Stage 1 + metadata-over-HTTP
  dict coercion fix.
- **2026-06-29** — Promotion bridge **Stage 1** (`gingugu promote`,
  `src/gingugu/promote.py`): MCP client that reads a source brain, applies the
  locked exclusion-based filter (verified, minus episodic/personal tags, minus
  secret-content), stamps provenance, and stores into a central brain
  idempotently. Also fixed a real latent bug — `metadata` on
  `memory_store`/`memory_update` now accepts a dict (HTTP transports deliver
  JSON objects as dicts; the `str`-only param had made remote metadata
  unusable). 16 new tests, 201 total. Verified live across two instances.
  Branch `feature/promote-bridge`.
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

- **Promotion bridge Stage 2-4** - consolidation with `contributors[]`,
  conflict detection, wiring to the real local brain (Stage 1 shipped, PR #11).
- Repo-ingestion agent to cold-seed central with org breadth.
- Data-ownership decision before hosting work-repo knowledge (personal vs
  company AWS, or scrubbed/synthetic seed).
- Phase 6 backlog (hybrid RRF retrieval, structured provenance) — see `docs/roadmap.md`.
