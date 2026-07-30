# Project Status

_Last updated: 2026-07-30_

## In Flight

- **Hook arg robustness + `default_repo` actually applying (v0.11.1, branch
  `fix/hook-arg-robustness-and-default-repo`)** — three shipped defects.

  1. **`Stop` hook crashed on foreign flags.** `parse_args()` makes argparse
     `sys.exit(2)` on anything unrecognized. That is a `SystemExit`, a
     `BaseException`, so the script's own `except Exception` never caught it —
     the try/except looked like protection and was not. Claude Code reads the
     non-zero exit as a blocked stop, so every session in an affected repo
     broke. Repos whose `settings.json` was written by other tooling append
     their own flags to the `Stop` hook routinely. Fixed with
     `parse_known_args()`, in the shipped template **and** this repo's own
     `.claude/hooks/stop.py`. Reproduced at exit code 2 before the fix.
  2. **`init` called incompatible wiring "already wired".** `_has_command`
     matched the bare filename `stop.py`, so a command pointing at another
     tool's same-named script counted as configured. Now inspects the flags in
     that command against the ones our script accepts and warns instead.
  3. **`init --force` silently clobbered a foreign `stop.py`.** Now backs it up
     to `stop.py.bak` and warns that the `settings.json` command may also need
     updating.

  Plus the 0.11.0 miss: **`default_repo` was inert.** Setting it changed the
  column and nothing else, and no supported path existed to apply it —
  `claim_rederive` was migration-side only, and `storage.update` re-syncs
  claims only when the prose actually changed. `memory_namespaces` update now
  re-derives that namespace's claims when the value changes, preserving
  resolution. `claim_rederive.rederive_claims` gained a `namespace_id` filter.

  393 tests, ruff + black clean.

- **Claim-extraction precision (v0.11.0, branch `fix/claim-extraction-precision`)** —
  two defects that shipped in v0.10.0, both found by dogfooding and both
  measured against the live 785-memory corpus before a line was written.

  1. **Refs inside `[[wiki-links]]` were read as assertions.** A link to a
     memory titled `PR #10 open: …` is a citation, not a claim. 11 wrong
     claims, **8 of them in `devex-ai-gateway`** — a namespace whose default
     repo was perfectly correct, so the existing namespace-containment
     guarantee never covered this one. Worst case: a memory titled
     `RESOLVED: internal gateway crashloop` asserting `#155 open`.
     All 11 drops were hand-checked against their source text; none were
     legitimate. When a claim's only state evidence sits inside a link, the
     linked memory already holds that claim, correctly keyed.
  2. **Every namespace was assumed to be a repo.** Bare refs in `crow` keyed to
     `crow#N`, a repo that cannot exist — 20 claims, all inert (contradiction
     detection is namespace-scoped, so they could only collide with each
     other). Fixed with `namespaces.default_repo`: unset falls back to the
     namespace name, a slug overrides, `""` means "not a repo".
     The `path` column was evaluated and **rejected** as a discriminator: 13 of
     17 namespaces have it NULL, including `gingugu` itself.

  **Migration 007** adds the column, seeds `crow`/`default`, and re-derives all
  claims. It goes through the new `claim_rederive.py` rather than
  `claim_sync.sync_claims` **because the latter drops `resolved_*` by design** —
  correct when prose changed, catastrophic here, where the prose is untouched
  and discarding resolutions would destroy unrecoverable manual reconciliation.
  **Rehearsed on a WAL-correct copy of the live brain**: 158 → 130 claims in
  ~200ms, 786 memories intact, re-run prunes 0. The single resolution lost is
  `crow#32`, a phantom row being deleted outright.
  386 tests, ruff + black clean.

  **Post-upgrade note:** namespaces that are not repos need declaring by hand —
  `bspeagle` still carries 2 mis-keyed claims because migration 007 seeds only
  gingugu's own conventions (`crow`, `default`), not user namespaces.

## Shipped / Working

- **Reconciliation backlog cleared (2026-07-30)** — the 10 claims that
  materialized when migration 006 ran against the live brain were resolved with
  `memory_update(resolve_claims=…)`, prose byte-identical: `devex-ai-gateway`
  #151/#166/#168, `gingugu` #11/#12(×2)/#13/#16/#20, `gingugu.com#1`. Open
  claims 30 → 20, contradicted 12 → 2. Every PR was verified merged with `gh`
  first — and three were nearly resolved against the **wrong repo**, since
  `Versaterm-Public-Safety/VersatermTechPlatform` also has merged PRs
  #151/#166/#168 for entirely different work. The namespace name is not the
  repo slug; check the PR title matches the memory.

- **Migration 006: claims-backfill repair (2026-07-30)** — the v0.10.0 backfill
  could never reach the dogfooding brain. Migration 005 originally only created
  `memory_claims`; the backfill landed a few commits later (6285739), but the
  live DB had already been stamped v5 on 2026-07-29 by the unmerged feature
  branch running against it. `migrate()` selects pending work with
  `current < target`, so 005 was permanently unreachable there and the table
  stayed empty — verified on the live file: `user_version` 5, 0 claim rows,
  776 memories. Migration 006 re-runs the backfill; it adds no schema.
  **Blast radius was one machine**: PyPI 0.10.0 shipped _with_ the backfill, so
  every real v4 → v5 upgrade populated correctly. This was self-inflicted by
  dogfooding branch code against the real brain.
  **Verified by rehearsal on a copy of the live DB**: 0 → 151 claims in 190ms
  (30 open / 121 resolved / 12 contradicted), re-run 0.0ms, 777 memories intact
  — matching the 30 / 118 / 12 predicted from copies before release.
  **Design note:** unconditional, not guarded on an empty table. A stranded DB
  that has since stored one memory with a ref is no longer empty, and an
  emptiness guard would skip it for good. Idempotence comes from
  `INSERT OR IGNORE` against `UNIQUE (memory_id, kind, ref)`, which also
  preserves any `resolved_*` state the user had already reconciled.
  **Standard written** into `.ai/standards/02-database.md`: a shipped migration
  can never be fixed in place, and dev instances must never point at a live DB.

- **State claims + write-time contradiction detection (v0.10.0, 2026-07-30)** —
  the structural answer to memories going stale. A memory that said "PR #10 open"
  was correct when written, so its prose is history; claims are extracted into
  `memory_claims` (schema v5) and resolution is recorded beside them instead.
  `memory_store`/`memory_update` return `contradicted_memories` at write time;
  `memory_stats` carries a `claims` backlog; `memory_update(resolve_claims=…)`
  reconciles with the body left byte-identical. No new MCP tool — the loop reuses
  the v0.8.0 fetch-by-ids sweep.
  **Origin:** Mr. Boomtastic rejected a plan to hand-edit 16 memories with
  `=== STATUS ===` banners as "a manual half ass workaround to make the header
  look a certain way." He was right — the corpus had 160 distinct banner styles
  across 37 memories precisely because the primitive was missing.
  **Measured before building:** an extractor prototype was scored against the
  live 764-memory corpus first. 10 of the stale PR claims already had their
  resolution sitting in the brain, written later and never linked — one pair on
  the same day. Live backlog: 30 open / 118 resolved / 12 contradicted.
  **Method note:** four bugs were caught by tests or re-measurement rather than
  shipped — a quote scanner that aligned on the wrong parity (matching the
  `", "` separators between quoted items), `"NOT merged yet"` reading as
  _resolved_ and inverting the claim, `shipped`/`superseded` being too ambiguous
  to sit in the resolved vocabulary, and a best-effort `try/except` that
  swallowed a total extraction outage into a log warning after a refactor.

- **Review-hint precision pass (v0.10.0, 2026-07-30)** — the detector fired
  on prose that merely contained the trigger words. Measured against the live
  751-memory corpus: precision 0.65 → 0.79. `waiting-on` now requires a named
  agent; no signal fires inside quotes or backticks; date signals defer to a
  `last_confirmed` that postdates them; deprecated memories are skipped on the
  read surfaces (they always were in `memory_stats` — the surfaces disagreed).
  `memory_update` gained a `type` param, which unblocks retyping a misfiled
  memory — the remedy agreed 2026-07-20 and impossible until now.
  **Method note:** the first benchmark counted deprecated memories and
  overstated the backlog by ~46%; a dated-snapshot exemption looked attractive
  until scoring showed it destroyed 17 true positives to remove 7 false ones,
  and was dropped. Score candidate rules against the corpus before building.

- **v0.9.1 (2026-07-29)** — hotfix: the 0.9.0 wheel shipped _without_ the bundled
  Memory Explorer, so `gingugu ui` was broken for PyPI installs. Plain `uv build`
  builds the wheel from the sdist, and `ui/dist` is gitignored so it never enters
  the sdist — the freshly-compiled UI was dropped even though CI had just run
  `npm run build`. `release.yml` now runs `uv build --sdist` and `uv build --wheel`
  separately so the wheel is built from the working tree. Verified by reproduction:
  plain `uv build` → 0 `_ui_dist` files in the wheel, `uv build --wheel` → 4.

- **`gingugu ui` (v0.9.0, PR #28)** — one command launches the Memory Explorer.
  Prod mode serves the built React bundle + a live `/api/export` on one port (no
  Node); the bundle ships in the wheel, added by a hatch build hook
  (`hatch_build.py`) only when `ui/dist` exists so CI builds without it. `--dev`
  runs the API backend + Vite hot reload. Also shipped: `pre_tool_use.py` rm-guard
  narrowed to block only catastrophic targets, not every `rm -rf <dir>`.

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
