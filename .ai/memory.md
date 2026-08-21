# gingugu

> A local MCP server that gives AI coding assistants persistent, structured, searchable long-term memory. One SQLite file, no cloud, no telemetry.

---

## What This Repo Does

- Exposes a **Model Context Protocol (MCP)** server — **stdio** by default, or
  **streamable HTTP** via `gingugu serve` (Bearer-token auth) for a hosted/central
  instance — that any MCP client (Claude Code, Claude Desktop, Cursor, Windsurf,
  Cline, …) can use as long-term memory.
- Stores memories in a single local **SQLite** database (FTS5 full-text +
  semantic embeddings) at the platform data dir — never inside the repo.
- Organizes memory in **two layers**: a global `crow` namespace (identity,
  cross-project wisdom) and one project namespace per repo.
- Models memory as a **graph** — memories link via typed relations, and recall
  uses spreading activation to wake related context.
- **Never forgets**: dormancy is a resting signal, not deletion. Only explicit
  `memory_forget` removes anything.
- Ships a **Memory Explorer** web UI (`ui/`) for browsing the graph and stats.

**Not** a cloud service. No backend servers, no API keys, no accounts.

---

## Tech Stack

- **Python** `>=3.11`; CI matrix ubuntu/macos/windows × 3.11–3.13
- **MCP** Python SDK (`mcp>=1.25,<2`); **stdio** (default) + **streamable HTTP**
  (`gingugu serve`, via `starlette` + `uvicorn`)
- **SQLite + FTS5** (WAL mode); semantic embeddings for hybrid retrieval
- **platformdirs** for the cross-platform DB path
- **uv**-managed; `ruff` + `black`; `pytest` + `pytest-asyncio` + `pytest-timeout`
  (suite is offline and bounded — see `.ai/standards/01-code-and-testing.md`)
- **UI:** React + Vite + Tailwind (`ui/`); served by `gingugu ui` (`webui.py`), built bundle ships in the wheel; `ui/api.py` is a thin dev shim
- Released to **PyPI** via Trusted Publishing (OIDC) on git tag

---

## Module Map (`src/gingugu/`)

| Module | Responsibility |
|---|---|
| `server.py` | MCP server entrypoint; `gingugu` (stdio) / `serve` / `promote` / `init` / `ui` dispatch; tool registration; must never crash |
| `serve.py` | `gingugu serve`: streamable-HTTP transport + Bearer-token auth + `/healthz` |
| `webui.py` | `gingugu ui`: serves the built Memory Explorer bundle + live `/api/export` on one port (prod, no Node), or spawns the Vite dev server (`--dev`); assets ship in the wheel at `gingugu/_ui_dist` |
| `promote.py` | `gingugu promote`: MCP client that promotes local "gold" to a central brain (filter + provenance + idempotent store) — not part of the server |
| `bootstrap/` | `gingugu init`: copies packaged hook/command/rules templates into a target repo (Claude Code hooks + non-destructive settings merge, or a `--client` rules file) — not part of the server |
| `bootstrap/global_rules.py` | Manages the memory protocol inside a marked block in the user-level `~/.claude/CLAUDE.md`. Append-only outside the markers; refreshes only its own block; refuses when an unmanaged protocol is already present |
| `bootstrap/_files.py` | Shared `read_template` / `safe_read` helpers, split out so `global_rules` doesn't import the package `__init__` that imports it |
| `bootstrap/settings.py` | Non-destructive `.claude/settings.json` merge. `declared_flags()` reads a hook's real `add_argument` flags off disk, so the "wired for a different script" warning reflects the installed script rather than our template's flag set |
| `config.py` | Config + cross-platform DB path (platformdirs); transport + credentials-flag settings |
| `database.py` | Connection, schema, WAL, migrations (`PRAGMA user_version`), FTS5 triggers |
| `models.py` | Memory / namespace / relation data models. Also owns `MEMORY_COLUMNS` - the one declared `memories` column list - plus `memory_columns_sql()` / `memory_placeholders_sql()`. Every module that reads or inserts a memory row derives its SQL from these; private copies drifted and silently dropped `pinned` |
| `storage.py` | Memory CRUD (store, update, forget) |
| `search.py` | True hybrid engine: BM25 pool, RRF fusion, composite re-rank. Ties break on id, never on iteration order |
| `semantic_pool.py` | The cosine ranking cohort, split out when `search.py` crossed 300 lines. `SEMANTIC_COHORT` / `ENTRANT_CAP` are FIXED constants: sizing them by `limit` made a memory's relevance depend on how many rows the caller asked for |
| `search_common.py` | Shared SQL columns + WHERE-fragment builders |
| `search_filters.py` | `advanced_search`: picks the retrieval strategy `sort_by` asks for - the hybrid engine, or one of the ordered listings |
| `search_listing.py` | The ordered-retrieval strategies: by column, by composite score, by FTS match set, by exact id. Each selects rows in the order it returns them; none re-sorts a pool truncated on another axis |
| `embeddings.py` | Semantic vector generation; owns `embedding_input()`, the one text recipe the write path and any compare path must share |
| `similarity.py` | ABSOLUTE payload-vs-memory similarity for the write-time hints: cosine, or token Jaccard without embeddings. Cutoffs are calibrated against a real corpus, never inherited from a ranking score |
| `context.py` | Session priming (`memory_context`): the pinned tier + three quota'd intent buckets, plus spreading activation |
| `relations.py` | Typed graph edges + hub-dampened 1-hop traversal (`dampened_neighbour_ids`) and enumeration (`list_edges`) |
| `relation_repair.py` | Edge repair mixed into `RelationManager`: `retype_relation`, `reverse_relation`, `delete_relation`, `delete_edges`. Every op is an UPDATE/DELETE on the existing row, so id / `created_at` / metadata survive a correction |
| `consolidation.py` | merge / summarize / deduplicate clusters |
| `decay.py` | Composite scoring, the `reference_timestamp()` freshness anchor (MAX, not COALESCE), dormancy as a resting signal (never auto-forgets), and `relative_age()`/`age_label()` — the derived-at-read `age` string |
| `stats.py` | Health stats (counts, confidence, dormancy, hygiene, review sweep) |
| `graph_stats.py` | Relation-graph health: edges, degree, type mix, orphans, and edges stranded past `SPREAD_PER_SEED`. Also `orphan_sample` (the orphans behind the count, costliest first) and the shared `orphan_filter()` predicate behind `memory_search(orphans=True)` |
| `staleness.py` | Advisory review hints for point-in-time memories |
| `claims.py` | Extracts checkable state claims (repo-qualified PR/MR refs) from prose; ignores refs inside `[[wiki-links]]` |
| `claim_sync.py` | Claim **write** path: persistence, contradiction lookup, resolution, and the storage bridge; resolves a namespace's default repo |
| `claim_queries.py` | Claim **read** path: the `claims.sample` backlog enumeration and the shared `claim_filter()` predicate behind `memory_search(claims=…)` |
| `claim_rederive.py` | Claim re-derivation that **preserves** resolution state, whole-corpus or scoped to one `namespace_id` (`claim_sync.sync_claims` drops resolution by design) |
| `namespaces.py` | Namespace CRUD; a `default_repo` change re-derives that namespace's claims (best-effort) so the declaration is not inert |
| `credentials.py` | OS-keychain credential vault |
| `portability.py` | Export / import a namespace |
| `handlers/` | MCP tool handlers: `memory.py` (store/update), `forget.py` (the one destructive tool), `hints.py` (write-time similar/relation hints), `recall.py` (recall/context), `search.py`, `relations.py` (relate/edges/unrelate) with `relation_ops.py` (batch parsing + per-edge dispatch), `consolidate.py`, `admin.py`, `credentials.py`, `helpers.py` |

Dev-only tooling at the repo root (never shipped in the wheel): **`bench/`** —
golden-set retrieval benchmark (Recall@K, MRR, precision, token cost;
deterministic, no LLM-as-judge). Committed synthetic fixture for CI regression;
real-brain golden sets + baseline reports live in gitignored `bench/local/`.
Run: `uv run python -m bench [--db <real-brain.db>]`.

---

## MCP Tool Surface

- **Memory:** `memory_store`, `memory_update`, `memory_forget`, `memory_recall`,
  `memory_search`, `memory_context`, `memory_stats`
- **Graph:** `memory_relate`, `memory_edges` (enumerate, read-only),
  `memory_unrelate` (retype / reverse / remove, single or batch, `dry_run`)
- **Lifecycle:** `memory_consolidate`, `memory_export`, `memory_import`,
  `memory_namespaces`
- **Credentials:** `credential_list`, `credential_get`, `credential_store`, `credential_delete`
  — gated by `MEMORY_CREDENTIALS_ENABLED` (default true); a shared/central
  instance runs with it `false` to omit the vault.

`memory_store` / `memory_update` return non-blocking `similar_memories` (merge
candidates) and `suggested_relations` hints. The latter are candidates to
**examine for a directional relationship**, not links to create: similarity is
how they are found, never the reason to wire them. Both are found by hybrid
retrieval and then gated on an **absolute** similarity (`similarity.py`: cosine
over embeddings, or token Jaccard without them), reported as `similarity` +
`basis`. A retrieval score could not do this job: it ranks within a pool, so its
best hit approaches 1.0 for any payload. An empty list is the common case.
Both are **always compact** — title + a ~200-char `summary`, never full bodies,
regardless of any caller flag. They are unsolicited extras on a write, so they
stay cheap; `memory_recall` fetches the body when a candidate matters.

`memory_update` accepts `type`, so a misfiled memory can be retyped instead of
reworded — retyping to `pattern`/`preference` is the sanctioned way to clear a
gated review-hint false positive, and it does not re-embed (the vector derives
from title + content only).

**State claims and the reconciliation loop.** `memory_store` / `memory_update`
also return `contradicted_memories` when the write resolves a ref another
memory still calls open — omitted, not empty, when there is nothing to report.
`memory_stats` carries a `claims` block (`open` / `open_actionable` /
`resolved` / `unverified` / `contradicted` plus a `review_limit`-capped `sample`
that **enumerates** the backlog, contradicted first, each row tagged). Reconcile with
`memory_update(resolve_claims="<ref>"|"all")`, which records the resolution and
leaves the body **byte-identical** — a dated log that said "PR #10 open" was
correct when written, so `content` is only for claims that were never true.
The loop: stats → `memory_search(claims="open")` (or `ids=…`) → `resolve_claims`.
`open` counts every unresolved claim; `open_actionable` excludes those on
deprecated memories and is what `sample` lists, so the two numbers explain any
gap between the count and the rows.

---

## Storage Model

- One SQLite file (default `~/.local/share/gingugu/memories.db`, platform-aware).
- `memories` table + FTS5 virtual table kept in sync by triggers.
- Each memory: `type` (fact/decision/pattern/bug/architecture/preference/workflow/context),
  `confidence` (verified/inferred/stale/deprecated), namespace, tags, timestamps,
  access count, content/title.
- Relations table: directed typed edges (`supersedes`, `related_to`, `caused_by`,
  `contradicts`, `parent_of`, `child_of`).
- `memory_claims` table (migration 005): one row per repo-qualified state claim a
  memory makes (`kind`, `ref` like `gingugu#10`, `state`). `state` is what the
  memory ASSERTS and is never rewritten; resolution lives in `resolved_state` /
  `resolved_by` / `resolved_at` beside it, so a claim can go stale without the
  prose being edited. Derived from text, so re-synced on any title/content change.
  Refs inside `[[wiki-links]]` are ignored — a citation is not an assertion.
  `state` is `open`, `resolved`, or `unverified` (migration 009): a ref the prose
  names without ever saying what became of it. `unverified` is excluded from
  `open`, `open_actionable`, `sample`, and contradiction detection — measured on
  1161 memories, 185 such claims exist against 223 real ones and nearly all
  narrate finished work, so counting them as open would bury the backlog in
  history. Read them with `memory_search(claims="unverified")`.
- `namespaces.default_repo` (migration 007): what a bare "PR #12" means in a
  namespace. Unset falls back to the namespace's own name (the
  one-namespace-per-repo convention, load-bearing: 145 claims vs 26 without it);
  a slug overrides it; `""` declares the namespace is not a repo at all, so bare
  refs are dropped rather than mis-keyed. `crow` and `default` are seeded `""`.
- Schema versioned via `PRAGMA user_version` (**currently 9**); migrations
  additive by default. Migration 006 adds no schema — it re-runs the claims
  backfill to repair DBs that reached v5 from pre-fix code and so can never
  run 005 again. Migration 007 adds `default_repo` and re-derives every claim
  under the corrected extractor, **preserving resolution state**. Migration 008
  adds `memories.pinned` (+ a partial index on the pinned rows only): memories
  that always load in `memory_context`, exempt from ranking. Defaults to 0, so
  an existing store changes no behaviour until something is explicitly pinned.
  Migration 009 adds no schema either (`state` was always unconstrained TEXT) —
  it re-derives every claim so state-less refs record as `unverified`, again
  preserving resolution. The mirror of 007: that one only removed claims, this
  one only adds them.

---

## Release State

- Current version: **0.17.0** (PyPI). Adds the `unverified` claim state (a ref
  a memory names without ever saying what became of it - excluded from
  `claims.open` and read via `memory_search(claims="unverified")`) and the
  orphan-enumeration + edge-reversal work (`graph.orphan_sample`,
  `memory_search(orphans=True)`, `memory_unrelate(reverse=True)`). Public repo
  `gingugu/gingugu`.
- Previous: **0.16.0** - edge repair: `memory_unrelate` (retype in place or
  remove) and `memory_edges` (enumerate a memory's relations).
- **0.15.0** - pinned memories: `memory_update(pinned=True)` marks a memory as
  unconditionally loaded by `memory_context`, exempt from ranking.
- **0.14.0** - `gingugu init` gained user-level rules management: the memory
  protocol lives in a marked block in `~/.claude/CLAUDE.md` that `init`
  manages, strictly additive outside its markers. Shipped alongside it: the
  `age`/freshness-anchor fix (`age` is anchored on `reference_timestamp` and
  elaborates to `"7 weeks ago (updated just now)"`; the anchor became a MAX
  instead of a COALESCE; a title/content rewrite now advances
  `last_confirmed`), the relation-discipline guidance reversal, and two
  `gingugu init` bootstrap fixes. Existing installs need `gingugu init --force`
  to pick up template changes - and as of the current unreleased fix, that
  command backs up anything it replaces.
- **0.13.0** - introduced the derived `age` payload field and fixed the
  startup contract's workspace inference.
- Two-layer namespace convention (`crow` + project) is live.
- See `.ai/plans/status.md` for in-flight work and carry-overs.
