# Project Status

_Last updated: 2026-08-26_

## In Flight

**The `memory_context` recency bucket orders by write recency -
`fix/recency-bucket-write-recency`, PR #61 (open).** 663 tests green (was 658),
`ruff` + `black` clean. Board item #1, the correctness bug that has sat at the
top of the board since 2026-08-21.

`context_buckets.recently_active()` ordered by `last_accessed`. That is a
**read** timestamp, so the bucket whose stated purpose is "a freshly-stored,
never-accessed memory always survives the cut" was in fact ranked by
familiarity - the opposite question. Reading promotes; writing does not.

**It was self-reinforcing.** `memory_context` touches everything it surfaces to
refresh the dormancy clock, so each session-start load lifted its own output to
the top of the next load's bucket. Anti-discovery bias in the bucket that
exists for discovery.

**No migration, and that is a deliberate narrowing of the approved design.**
The design settled on separating dormancy from bucket ordering rather than
patching one column to mean both things. Reading the write surface first showed
the second column already exists: `grep "UPDATE memories"` returns exactly three
statements, two of which touch only `last_accessed` (`record_accesses`,
`touch_many`) while the third is an explicit edit. So `updated_at` moves only on
a deliberate write and cannot self-reinforce. A new column would have duplicated
it. `MEMORY_COLUMNS`, the `Memory` model and the schema are untouched.

The accepted trade: a confidence-only or metadata-only edit lifts a memory in
the bucket. "Someone worked on this recently" is a legitimate discovery signal,
and it is not the pathology being fixed, which was a *read* promoting itself.

`last_accessed` is unchanged and still correct for what it is actually for -
dormancy and the access signal. `handlers/recall.py`'s `touch_many` is
deliberately left alone: it is now harmless to the bucket and remains right for
its real purpose.

**Measured on a real 295-memory namespace:** the old key returned a
chronologically scrambled window (position 2 from Aug 21, position 5 from Aug
27, position 8 from Aug 17) that omitted both the newest handoff and the
current board entirely. The new key returns them at positions 2 and 4, newest
first, with only 4 of 10 rows in common. This retires a standing workaround
carried in every handoff memory: "do not topic-recall for the resume, read the
ID off `memory_context` and fetch by ID" existed because the newest handoff was
structurally unreachable.

**Regression evidence:** `tests/test_context_buckets.py` is new (4 tests) and
gives `recently_active()` its first direct coverage - the existing tests all
reached it through `build_context`, whose quota machinery can fill a slot from
the backfill pool and mask a wrong `ORDER BY` underneath. Plus two in
`tests/test_context.py`. Neutered the fix with all six in place: **4 of 6 fail.**
The other two document filtering and namespace scoping, which this change does
not touch.

One test was written for this PR and then **cut rather than kept**: it asserted
that a context load's own `touch_many` buries a newer memory, and it passed
against the unfixed code. It could not fail, because that load also touches the
newcomer. The burial needs reads of *other* memories after the write, which is
exactly what the surviving high-access test proves.

**A red CI run then exposed four tests that were racing the clock.** The first
push went green on ubuntu and macOS and red on `windows-latest` 3.11 and 3.12.
Windows resolved `datetime.now()` to 15.6ms until Python 3.13 switched to
`GetSystemTimePreciseAsFileTime`, which is why 3.13 sat green beside 3.11 and
3.12 red - a version boundary, not a flake. Consecutive `store.create()` calls
land inside one tick there and come out with a byte-identical `updated_at`, so
every test that stored memories and then asserted an ordering by write time was
asserting a coin flip.

**A `rowid DESC` tiebreak was tried first and was wrong.** It turned the
originally-failing test green and broke two others, which is the useful part:
`rowid` is *creation* order and an edit never moves it, so on a tie it answers
"created later" - the exact question this bucket was just fixed for not asking.
The edit tests had been passing on rowid-ascending ties by accident. Nothing
stored records write order, so there is no correct tiebreak to reach for, and
the ordering now documents the tie as unspecified rather than resolving it
wrongly. Which of two memories written inside one tick is newer is not knowable
from the data.

**The tests supply their own timestamps instead.** A `backdate` fixture
(`tests/conftest.py`) stamps memories with well-separated past `updated_at`
values, ending a day in the past so a subsequent real `store.update()` lands
strictly later and an edit genuinely leads. `tests/test_context_ordering.py`
carries a local equivalent because it reaches the server over MCP rather than
through the `store` fixture.

**Verified by simulating the failing platform**, not by re-running CI: a
throwaway autouse fixture monkeypatched `utcnow_iso` to a 15.6ms-granular clock
and ran the full suite. It reproduced the Windows failures on macOS, and caught
two more clock-dependent tests that Windows CI had passed only by luck
(`test_context_fresh_memory_survives_high_access_competition`,
`test_freshest_memory_is_not_buried_by_its_placeholder_score`). Suite is green
under both the simulated coarse clock and the real one.

`pinned()` and `cross_namespace_patterns()` sort on `last_confirmed`/
`created_at` and `access_count` respectively and have the same unspecified-tie
class. Deliberately out of scope here; worth a board item.

## Shipped to `main`, awaiting release in v0.18.0

**Per-hit score breakdown + `memory_excerpt` - PR #60, merged `b9fc4f1`
(2026-08-26).** 658 tests green (was 634), `ruff` + `black` clean. Board items #2 and #3, shipped together: both are arithmetic and
string handling on the retrieval surface, and neither needs a migration.

**The breakdown is an instrument, not a feature.** Every read surface can now
return `score_breakdown` under `explain=True`: the weighted
`relevance`/`freshness`/`access`/`confidence` terms that `score` is the sum of,
plus `type_boost` where `memory_context`'s architecture/decision boost applied.
Diagnosing the ranking defect currently at the top of the board took a backup of
the live database and driving `build_context` from source, because a single
blended float says nothing about which term produced it. The same class of
question is now answerable from an ordinary tool call.

`composite_score` is summed from a new `composite_parts`, and `score_memory`
from a new `score_parts`, so a reported breakdown and the score it explains
cannot drift - they are the same arithmetic, not two implementations of it.
The terms are weighted contributions rather than raw components for the same
reason: they add up to the number they are explaining, and a caller reading
them does not have to know the configured weights.

**What the breakdown makes visible immediately:** the recency and
cross-namespace buckets are scored on a synthetic `relevance=0.5`, so several
hits sharing one identical relevance term did not match the task hint at all -
they were selected for recency or reach. That was true before and invisible.
`test_synthetic_relevance_is_visible_as_a_flat_term` pins it.

It is opt-in because every always-on field is paid for on every hit of every
read, and "why did this rank here?" is asked rarely and deliberately. Results
with no ranking behind them carry no breakdown rather than a fabricated one:
pins never entered the ranking, an `ids` fetch was not ranked, and a bare fused
relevance has no composite to decompose.

**`memory_excerpt` reads inside one memory.** Between a full body and a
~200-char compact summary there was nothing, and our memories run to several KB
- asking whether one mentions the release policy, and where, meant pulling every
byte of it into context. Two composable modes: `query` for a literal
case-insensitive scan returning each match with offsets, 1-indexed line, and
surrounding context; `start`/`end` for an exact character slice. Passing both
searches inside the range with offsets still absolute, so a hit can be fed
straight back as a range read. `total_matches` reports the true count even when
`max_matches` caps the payload, which is what lets a caller tell "that was all
of them" from "that was the first 10 of 300".

Deliberately dumb: literal substring matching, no ranking, no stemming, no
model. Asking twice gives the same answer in the same order.

**Structural, and it is pre-existing debt paid down:** `context.py` was already
over the 300-line limit on `main` (305) and this work pushed it to 320. The
three bucket-fetch queries moved to a new `context_buckets.py`, leaving
`context.py` at 271 - the split falls on a real seam, since that module decides
how buckets are combined and quota'd while the new one only says where their
rows come from. `test_memory_columns.py`'s private-copy guard follows the SQL.

**Regression evidence:** `tests/test_score_breakdown.py` (10 tests) and
`tests/test_excerpt.py` (14 tests). The load-bearing ones are the sum
invariants - the reported terms must add up to the score they explain, at the
arithmetic layer and over the live tool surface - and `type_boost` being its own
term rather than folded into another, since a breakdown that does not add up is
worse than no breakdown at all.

**`memory_import` embeds the memories it writes - PR #59,
merged `0cabfdf` (2026-08-21).** 634 tests green (was 627), `ruff` +
`black` clean.

Everything restored from a backup was reachable by keyword only. The FTS5 index
has triggers and keeps itself in step with `memories`; `memory_embeddings` has
none, so a vector exists only where some code path deliberately wrote one - and
`import_data` writes memory rows with raw SQL and never touched embeddings.

**The startup backfill was not the repair path people assumed.** It drains ONE
batch of 32 per process. Measured: a 272-memory namespace needs 9 server
restarts to become searchable, a 1,423-memory brain needs **45**. The comment
claiming "subsequent recalls will surface the rest naturally as memories get
embedded on write" is false for exactly this case - imported rows are never
written again. That correction matters more than the count, because it is why
the bug survived: the drip looked like a safety net and was not one.

**The decision worth flagging:** vectors are recomputed on arrival rather than
carried in the export payload, so **the export format is unchanged**. They are
model-specific - a 384-dim BGE export restored on a host running a 768-dim
model would have to be discarded on arrival - so shipping them would add ~1.5KB
per memory to a file meant to stay portable and legible, for data derived from
the text sitting right beside it. The trade is a slower import; correctness by
construction is worth it.

Encoding runs **after** the commit. The memories are the payload and the
vectors are a bonus, so the two must never be traded: an import with a broken
or absent embedder still succeeds and leaves the rows eligible for the backfill.

**Structural change, and it is the actual root cause.** The embedding logic
lived inside `MemoryStore`, which made it unreachable for the *other* module
that writes memory rows. It now lives in `embedding_sync.py`, which takes
`(conn, embedder)` so any writer can honor the invariant without depending on
the CRUD layer. `storage.py` drops 493 -> 388 (still over the 300 limit, but a
big bite out of pre-existing debt) and its embedding methods are thin
delegations, so the public API is unchanged. `similarity.py` was hand-rolling
the same `memory_embeddings` read and now uses `embedding_sync.get_many`,
removing a fifth site that had to remember the mismatched-model filter.

`embed_ids` is deliberately distinct from `backfill`: it finishes the list it
is given, while `backfill` drains one batch by design because it runs at
startup where a cold model download must not block the process.

**Regression evidence:** `tests/test_import_embeddings.py` (7 tests). Neutered
the fix with the tests in place: **3 of 7 fail**, reporting 0 embeddings after a
3-memory import, 0 after a `replace`, and an empty vector map through the
storage layer. The other four are guards - the no-embedder path, the broken-
embedder path, the skip path (a skip wrote nothing, so it must not claim to
have embedded anything), and `embed_ids` clearing 70 ids where `backfill`
clears 32.

**Write-time hints report an absolute similarity, not a rank artifact - PR #58,
merged `3ebe9f2` (2026-08-21).** 627 tests green (was 619), `ruff` +
`black` clean.

`similar_memories` and `suggested_relations` reported the fused RRF relevance
from `search()`. That number is a function of a candidate's *rank* in the BM25
and semantic pools, normalized so rank 1 in both maps to 1.0. Something is
always rank 1, so the top hit trended toward 1.0 for every payload ever
written, and both gates - 0.5 for merge candidates, 0.3 for relation candidates
- sat below what the arithmetic could even produce. Neither ever rejected
anything: **every store returned six candidates**, each costing a read to
dismiss.

Measured on a read-only copy of the live brain (1,423 embedded memories), the
payload "Lunch was a tuna sandwich" scored **0.9262** against a corpus of
engineering notes. Two other unrelated payloads, one of them in a different
namespace, scored 0.9262 as well - identical to four decimals, because all
three landed on the same rank pair. A genuine paraphrase of an existing memory
scored 0.9841. The entire usable range was 0.058 wide; cosine separates the
same pairs by 0.33.

**The decision worth flagging:** retrieval and adjudication are now separate
stages, and the split is the fix. RRF is kept for *finding* candidates, which
is what it is good at. An absolute measure (`similarity.py`) then rescores the
survivors and owns the gate. Hits report `similarity` + `basis` and no longer
carry a retrieval `score` - two numbers under one payload, one of them
meaningless, is worse than one number that means what it says. This is a
visible change to what the tool returns.

**Cutoffs are calibrated, not chosen by feel.** Positives: 228 `supersedes`
pairs from the live brain. Negatives: 7,688 random same-namespace pairs. Cosine
`0.80` admits 8.5% of random pairs while keeping 84.7% of genuine
near-duplicates; token Jaccard `0.15` lands at 8.7% / 84.2%, the same operating
point in the other instrument - so turning embeddings off changes precision,
not the meaning of the gate. Relations sit softer at `0.72`/`0.10`. The
calibration is corpus-specific on purpose: BGE cosine does not bottom out near
zero, and two unrelated memories from one brain sit around 0.71 from shared
register alone.

**Effect, measured end to end on the same five payloads:** hints emitted across
five stores fell from **30 to 10**. All three nonsense payloads now return
empty lists; the real duplicate still surfaces all three of its hits.

**Regression evidence:** `tests/test_hint_similarity.py` (7 tests). The two a
rank-based score cannot pass are `test_unrelated_payload_gets_no_hints` (an
unrelated payload gets an empty list even though retrieval still hands over its
best candidates) and `test_similarity_does_not_depend_on_the_rest_of_the_pool`
(the same pair reports the same number alone and buried in a crowded pool).
`test_hints_do_not_leak_a_retrieval_score` pins the payload change.
`tests/test_suggest_relations.py` was rewritten: it used to stamp `mem.score`
on a fixture to drive the gate, which now tests nothing, so candidates pass or
fail on their text instead.

**Structural change:** `helpers.py` was 393 lines, well over the 300 limit, so
both hint builders moved to `handlers/hints.py` (helpers drops to 266). The
tool-surface docs the fix requires pushed `memory.py` from 302 to 313, so
`memory_forget` moved to `handlers/forget.py` - a real seam, since it is the
only tool in the package that can remove a memory. Every touched module is now
under 300. `embeddings.py` gained `embedding_input()`, the single text recipe
the write path and the compare path must share; a drift between them would not
raise, it would quietly compare vectors built from different text.

**`memory_context` presents what it selected, instead of re-sorting it - PR #57,
merged `4169980` (2026-08-21).** 619 tests green (was 614), `ruff` +
`black` clean.

Two defects in the same presentation code, one PR. `context.py` prepended the
pinned tier correctly and `handlers/recall.py` then sorted the merged result by
`m.score or 0.0` - and a pin carries no score by design, so every pin sank to
the bottom of every payload. Measured on a copy of the live brain with a
`crow,gingugu` load: **0 of 8** pins in the top 8 before, **8 of 8** after. The
pins were at positions 20-27 of 28.

The same sort also ranked buckets against each other on a number they do not
share. Only the task bucket has a real search relevance; the recency and
cross-namespace buckets carry a fixed `relevance=0.5` placeholder, because they
have no query to be relevant to. So the recency quota would guarantee the
freshest memory a slot and the sort would then show it below every task hit -
which is the same as not guaranteeing it.

**The decision worth flagging:** selection order and presentation order are now
explicitly different questions. Selection still fills recency _first_, because
that is what prevents eviction under a contended `limit`. Presentation emits by
bucket _membership_ - task, then recency, then cross-namespace, then the
score-ordered backfill - so the caller's question is answered at the top and the
fresh anchor sits right behind it. Membership rather than which quota claimed
the row: recency is filled first, so a task-relevant memory that is also recent
was otherwise presented as though it had never matched the query at all. That is
what `test_context_task_hint_prioritizes` caught, and it was right to.

Multi-namespace merging no longer globally sorts either. Composite scores are
not comparable _across_ namespaces (different corpora, different access and age
distributions), so `_merge_namespace_context` puts every namespace's pins first
and then interleaves the ranked tails by rank position - preserving each
namespace's internal order without burying the second one's freshest material
under the first one's entire list.

Each bucket also gained a deterministic total order - native signal, then
composite score, then `id`. CI caught the need for it: `test_context_type_boost`
failed on two Windows runners and passed on the other seven. The two fixtures
tie exactly on `last_accessed` (coarse clock, same tick), and the old global
score sort had been masking that tie via the type boost. Removing the sort
exposed a latent flake and, with it, the fact that the architecture/decision
boost no longer influenced order at all. Comparing scores *within* a bucket is
sound - every row got its relevance the same way - so the boost lives there.

**Regression evidence:** `tests/test_context_ordering.py` (5 tests), asserting
POSITION in the payload over the live tool surface - a layer where the existing
pin tests stopped, since they assert position against `build_context` directly
and only membership through the handler. Reverted the source with the tests in
place: **3 of the 5 fail**, reporting the pin at index 5 of 5, both pins at 10
and 11 in a two-namespace load, and the freshest memory below the guaranteed
region. `test_identical_timestamps_break_deterministically` forces the exact tie
the Windows runners hit and fails without the per-bucket tiebreak. The remaining
one (`test_second_namespace_is_interleaved_not_appended`) passes against the old
code too and says so in its docstring - the old global sort also mixed
namespaces, so it guards the replacement merge rather than the defect.

**`sort_by` sorts the corpus, not a pool truncated on another axis - PR #56,
merged `5229dce` (2026-08-21).** 614 tests green at merge, 9/9 CI.

`memory_search(sort_by="created")` returned the newest rows *of a candidate
pool* that had already been cut by a different ordering: `limit * 4` rows by
relevance with a query, or by `last_accessed` without one, re-sorted in Python
afterwards. Anything that lost that first cut was unreachable however new it
was. A date sort has exactly one correct answer, and this returned a provably
wrong one.

Measured on a copy of the live brain before the fix: `sort_by="created",
limit=5` in `crow` returned **0 of the 5** correct rows, two of them three weeks
older than what belonged in those positions, while the true newest was minutes
old. With a query, `"RESUME"` in `gingugu` at limit=5 missed the two newest
matching memories outright. After the fix both branches match the SQL ground
truth exactly, 5 of 5, and the prefix invariant holds:
`ids(limit=k) == ids(limit=K)[:k]`.

Each ordering is now its own strategy in a new `search_listing.py`, and every
one of them selects rows in the order it returns them. A column sort orders the
whole matching corpus in SQL before the limit. A score sort - which SQLite
cannot order, since the composite is computed in Python - scores every matching
row, reading six columns and no bodies, then fetches bodies for the winners
alone. `search_filters.py` is now just the dispatcher that picks between them
(117 lines, down from 161).

**One semantics decision worth flagging.** With a query, a date sort now runs
over the FTS match set alone: no BM25 ranking and no semantic cohort. A date
asks something relevance cannot answer, and cohort membership is itself a
relevance judgement, so including it would leave the corpus defined by the very
axis the caller asked to sort *instead* of. Those results carry no `score`,
because there is no ranking behind them to report. Relevance sorts are
untouched.

`_CANDIDATE_MULTIPLIER` is gone from `search.py` - it existed only to
oversample this pool, and PR #55's fixed cohort had already made it redundant
for relevance sorts.

**Regression evidence:** `tests/test_sort_by_truncation.py` (8 tests). Reverted
the source with the tests in place: **7 of the 8 fail**, each because the row
that should have won is absent rather than mis-ranked. The eighth
(`test_score_sort_without_weights_falls_back_to_listing_order`) passes against
the old code too and says so in a comment - it pins a new fallback branch and is
not a regression guard.

**Recall relevance no longer depends on `limit` - PR #55, merged `1e84260`
(2026-08-21).** 606 tests green at merge, 9/9 CI.

`search(q, limit=k)` was not the first k of `search(q, limit=K)`. The semantic
cohort was sized `limit * 4` plus `limit // 2` entrants, so a memory's semantic
RANK - and therefore its relevance, and therefore the result order - moved with
the number of rows the caller asked for. A caller narrowing the request to be
precise got a different, worse answer.

Measured on the real brain across 5 queries x 2 namespaces: **8 of 10 pairs
unstable, 4 of them returning a different top-1 memory purely from the limit.
After the fix, 0 of 10**, with the full prefix invariant holding.

The cohort and entrant cap are now fixed constants in a new `semantic_pool.py`,
set to the geometry at the benchmarked depth (`bench/` issues one call at
limit=10, giving a 40-row pool and a 5-entrant cap). Verified: a limit=10 call
returns identical ids and identical scores before and after, so the recorded
real-brain benchmark still describes this code. Every other limit now behaves
the way the benchmarked one already did.

**A second defect surfaced while measuring the first.** Comparing old against
new at depth 10 showed 2 of 10 pairs differing - then 0 of 10 on a rerun of the
same comparison. The variance was the finding: RRF maps a swapped rank pair to
identical floats, so exact ties are routine, and the order among tied memories
came from `_fuse_ranks` iterating a `set`. The same query on the same data
returned tied memories in a different order from one process to the next. Ties
now break on id.

**On the benchmark:** `uv run python -m bench` reports `retrieval: bm25-only`
and is unchanged (MRR 1.000 on the fixture). That confirms no BM25 regression
and nothing more - the fixture bench runs without embeddings, so it is
structurally blind to a semantic-cohort change, the same way it was blind to
call depth. Do not read a green fixture run as evidence about this path.

**One column list for `memories` + a WAL-safe pre-migration backup - PR #54,
merged `fd5bcfa` (2026-08-21).** Two data-integrity defects, one PR. 589 tests
green at merge, 9/9 CI.

1. **The column list had drifted into four private copies.** `storage.py` and
   `context.py` carried `pinned`; `search_common.py` and `portability.py` did
   not. Nothing failed loudly, because a short list still parses and
   `Memory(**row)` still constructs - the field simply took its default. So
   every search path reported a confident `pinned=False` for genuinely pinned
   memories, and `memory_export` dropped the flag outright. Measured against the
   live brain: 7 pinned rows in `crow`, `0` surviving an export. Export/import
   is the documented backup path, so restoring a backup silently unpinned
   exactly the memories marked never-lose-these.

   Now declared once in `models.py` as `MEMORY_COLUMNS`, with
   `memory_columns_sql()` / `memory_placeholders_sql()` so an INSERT's VALUES
   clause is generated from the same tuple as its columns. `pinned` is restored
   on import, and an export written before the column existed still imports
   (absent means `0`, since `pinned` is `NOT NULL`).

2. **The pre-migration backup was not WAL-safe.** `_backup_before_migration`
   used `shutil.copy2`, which copies `memories.db` and leaves `memories.db-wal`
   behind. Under the real conditions the resulting backup did not even contain
   the `namespaces` table - the entire schema was still in the WAL. That file is
   the only safety net if a migration goes wrong. Now uses `conn.backup()`,
   which is WAL-aware and consistent under concurrent writers (two sessions
   sharing a brain is normal here).

   `.ai/standards/02-database.md` **already carried this rule**, written for the
   manual rehearsal workflow and never applied to the shipped code path. The
   standard now says so explicitly.

**Guard against the recurrence:** `tests/test_memory_columns.py` holds
`MEMORY_COLUMNS` against both `Memory`'s fields and the live SQLite schema, so a
migration that adds a column without teaching the readers fails CI. Verified by
deliberately removing `pinned` from the tuple: 4 tests go red, structural and
behavioural.

**Bootstrap `--force` data loss - PR #53, merged `2646666` (2026-08-21).** Three
defects in the `bootstrap` package, one PR. All three were live in released code
(v0.14.0 through v0.17.0, on PyPI); the fix reaches users when v0.18.0 ships.
581 tests green at merge, `ruff` + `black` clean, 9/9 CI.

1. **`_write_file` lost its backup net the moment the net first worked.** The
   `.bak` was written only when the target _lacked_ `gingugu-init:managed-file`,
   so the opening `--force` backed the file up and stamped the marker, and every
   `--force` after that saw its own marker and overwrote the file - the user's
   edits with it - silently. Proven by experiment: init a repo, edit a managed
   hook, `--force`, and the edit is gone with no `.bak` on disk. Whether a file
   is _ours_ says nothing about whether it has since been _customized_, so the
   backup now keys off content changing, not ownership. An unchanged file still
   writes no `.bak`, so re-running `init` does not litter.

2. **The `--client` rules-file path had no backup on any branch.** `--force`
   wrote the protocol template straight over `.windsurfrules` / `.cursorrules` /
   `.clinerules`. Strictly worse than (1): those files are hand-authored from
   line one and were never `init`'s to replace, and no test covered the path.
   Found while scoping the fix for (1), not previously recorded. Both write
   paths now go through `_write_file`, so there is one backup rule rather than
   two that can drift.

3. **Three strings advertised `gingugu init --global`,** a flag the parser
   rejects and `test_global_flag_is_not_a_thing` asserts must `SystemExit`. The
   worst was `_MANAGED_NOTE`, which is written _into_ the user's own
   `~/.claude/CLAUDE.md` - shipping a file that tells the reader to run a command
   that errors. Strings corrected (the flag was never added; the step is
   deliberately not opt-in) and a test now fails if any shipped surface names it
   again.

**Worth carrying beyond that PR:** `test_reforce_over_our_own_file_makes_no_backup`
asserted defect (1) _was correct_ and was green in CI the entire time the
behavior was destroying files. 575 passing tests were never evidence that path
was safe - the suite was holding the bug in place. The test is inverted, not
deleted. See `.ai/standards/01-code-and-testing.md`.

Both of these PRs fixed a **silent** defect that a green suite was compatible
with: one because a test asserted it, one because a short column list still
parses. Neither would have been caught by running more of the same tests.

## Shipped in v0.17.0 (2026-08-14)

Released to PyPI on tag `v0.17.0`. Carries two features: the `unverified` claim
state (#52) and the orphan-enumeration + edge-reversal work (#51), which merged
after v0.16.0 had already been cut and so waited for this release.

- **The `unverified` claim state (#52, squash `b2cc479`).** Closes the last
  product gap found by running step 3: the claims heuristic never fired on a
  ref whose prose asserted no state, so a memory reading `PR #1: <url>` under a
  "Deliverables" list produced zero claims and read as in-flight to a human
  forever.

  **The fix the corpus ruled out.** The obvious move — treat a state-less ref
  as `open` — was measured first and rejected. Over 1161 memories, 225 ref
  *mentions* (185 distinct claim rows) are named with no asserted state against
  223 real claims, and they overwhelmingly narrate finished work: *"Fixed in
  PR #873"*, *"PR #121 deployed successfully"*. Defaulting them to open would
  more than double the backlog with history, which is precisely the failure
  `claims.py` already refuses in its own docstring: a missed claim is silent,
  a wrong one teaches the reader to ignore claims entirely.

  So a state-less ref gets a third state, `unverified`, asserting only *"this
  memory names a ref and never says what became of it"*. It is excluded from
  `claims.open`, from `open_actionable`, from `claims.sample`, and from
  contradiction detection, and is read through `memory_search(claims=
  "unverified")`. A browsable index, not a queue.

  **Two sub-calls.** `resolve_claims="all"` stays open-only — sweeping an
  unverified ref under "all" would record that the caller verified something
  they never looked at; naming the ref explicitly still resolves it, a path
  that already worked unchanged. And `unverified` stays out of `sample`,
  because listing it beside open claims would present history as work.

  **No DDL.** `memory_claims.state` is plain TEXT with no CHECK constraint, so
  migration 009 is a pure `claim_rederive` pass — the mirror image of 007,
  which only ever removed claims where this only ever adds them, making
  `_prune` a no-op. Verified against a copy of the live brain: `open` 0 → 0,
  `resolved` 188 → 188, 43 existing resolutions preserved, 185 unverified rows
  gained. 575 tests passing (+14). Ruff + black clean. Tool surface stays 18.

- **Orphan enumeration + edge reversal (#51, squash `e689200`).** Merged
  2026-08-14, one release behind the work: it landed after v0.16.0 was cut, so
  it ships here. Recorded as in flight for three sessions after the fact, which
  is the doc-lag this entry closes. Both halves closed gaps found by *running*
  the 3A sweep below, not by a test.

  - **Orphan enumeration.** Third instance of the invisible-backlog pattern,
    after the `related_to` mix (fixed by `memory_edges`, #49) and the claims
    backlog (#47). `memory_stats.graph` reported an orphan count and nothing
    could name a single one, so working the backlog meant raw SQL against the
    live brain. New `graph.orphan_sample` (confidence → access count → recency,
    namespace-stamped, raised by the existing `review_limit`) plus
    `memory_search(orphans=True)` for the same set with full bodies. One shared
    `graph_stats.orphan_filter()` predicate behind both, on the `claim_filter()`
    precedent.

    **Design note:** deprecated orphans sink to the bottom of the sample rather
    than being filtered out. That deliberately avoids the `open` vs
    `open_actionable` two-number problem the claims work needed — one
    population serves the count and the list, so no gap needs explaining.

  - **Edge reversal.** `memory_unrelate(reverse=True)` swaps an edge's
    endpoints on the existing row, preserving id / `created_at` / metadata like
    a retype, and combines with `new_relation_type` in one write. The 3A sweep
    hit ~11 edges that were correctly connected but backwards, several also
    mistyped; each cost a delete plus a re-`memory_relate`, which discards the
    provenance the repair path exists to protect.

  - **Two forced splits.** Both target files crossed 300 lines as a direct
    result: `relations.py` (351) → `relation_repair.py` (repair ops, mixed into
    `RelationManager`), and `handlers/relations.py` (332) →
    `handlers/relation_ops.py` (batch parse + per-edge dispatch). Conceptual
    seams, not arbitrary cuts; public API unchanged, tool surface stays at 18.

  561 tests passing (+29: 13 reverse cases in `test_edge_repair.py`, 7 sample
  cases in `test_graph_stats.py`, and a new `tests/test_orphan_enumeration.py`
  mirroring `test_claim_enumeration.py`). Ruff + black clean. No schema change,
  no migration, every new parameter defaults off.

  **Feeds 3B** under *Blocked / Pending*: the whole argument for building this
  before the content read was that orphan enumeration is how 3B gets
  prioritized.

- **Memory cleanup 3A: the `crow` structure pass — COMPLETE (2026-08-13).**
  Recorded here two sessions late; the work touched only the memory store, not
  this repo, which is exactly why it kept missing the doc pass.

  All 394 `crow` `related_to` edges were read and judged individually across
  nine rounds. `high_signal_ratio` **0.304 → 0.832**; `related_to` 394 → 80;
  edges 566 → 475; `over_spread_cap` 95 → 49. It was the first real use of
  `memory_unrelate`, on the exact edge it was built for.

  **Orphans rose 38 → 44 and were deliberately not papered over** — deleting a
  pure-topic-adjacency edge is the honest outcome even when it strands a
  memory, and inventing a replacement edge to keep the metric flat would be the
  precise failure the relation-discipline pass exists to prevent. That is the
  backlog the orphan enumeration above now makes workable.

  **Standing decision amended at round 1:** per-edge deletion of pure topic
  adjacency is approved. The no-bulk-prune decision under *Blocked / Pending*
  stands unchanged — it forbids criteria-driven sweeps, not judged deletions.

## Shipped in v0.16.0 (2026-08-13)

Released to PyPI on tag `v0.16.0`. Companion site update rides in
gingugu.com#3 (tool grid 16 -> 18, plus an `[edge_repair]` feature line),
merged after this release so the site never advertises a tool the published
package does not carry.

- **Edge repair: `memory_unrelate` + `memory_edges` (#49, squash `7b4f367`)** —
  closes the gap that blocked the 3A structure pass. The relation surface was
  create-only: nothing removed or relabelled an edge, so a wrong one was
  permanent for the life of both memories, and since spreading activation
  visits at most 3 neighbours per seed without weighting by type, it kept
  competing for a slot against a right one forever. Precision demanded, errors
  unfixable, cost paid on every recall. Two instances hit in two days while
  writing edges by the very guidance that demands the precision.

  - `memory_unrelate` — retype in place (UPDATE, so id / `created_at` /
    metadata survive) or delete. Retyping onto an existing type collapses the
    pair and reports `merged`, not `retyped`. Batches of up to 100 reviewed
    ops, validated whole before any write; `dry_run` previews.
  - `memory_edges` — the discovery half. `memory_stats.graph` reported *that* a
    graph was mostly `related_to` and nothing could say *which* edges those
    were, so repair meant hand-written SQL against the live brain. Same shape
    as the claims gap (#47): a metric with no enumeration behind it.
  - `relations.py` gains `retype_relation`, `delete_edges`, `list_edges`. The
    long-dead `delete_relation` (tested since it was written, never called by
    anything) finally has a caller.
  - `handlers/relations.py` was carrying `memory_consolidate`; it moved to a new
    `handlers/consolidate.py` to keep both under the 300-line limit. No
    tool-surface change from the split — same tool, same name.
  - **Deliberately not built:** a criteria-driven bulk retype. The point of
    retyping is that each edge deserves a different type based on what it
    records; a blanket relabel would manufacture directional claims that were
    never true, and a false `caused_by` retrieves worse than an honest
    `related_to`. Batching saves round-trips, not judgment.
  - `edges` is typed `list[dict]`, not a JSON string: FastMCP pre-parses
    JSON-looking strings before pydantic validation, so a `str`-annotated
    param can never receive a JSON array.

- **Comparison content retired from the public surface (#48, squash
  `23e254e`)** — the README's `How It Compares` section and its
  TOC entry are gone, and the matching `cat comparison.txt` block came out of
  gingugu.com in the same pass (with its now-orphaned `.cmp-note` CSS).

  The section had already shed its capability matrix on 2026-07-07; what remained
  was a fair, hedged paragraph naming four other projects. Fairness was never
  the problem. Naming them at all made the page partly about them, and it
  committed the docs to tracking someone else's roadmap to stay accurate.
  Positioning is now stated in absolute terms only.

  Docs-only, no `src/` change. Two things left deliberately in place: the
  CHANGELOG's historical entries about the old matrix (shipped history is a
  record, not marketing copy), and the FAQ entry on editor built-ins (it exists
  to explain what `gingugu init` gives you, not to rank a competitor).

- **Claims enumeration (#47, squash `5b0a304`)** — closes a product gap
  found by dogfooding, not by a test: `memory_stats.claims.sample` reported a
  count of open claims and then listed only the _contradicted_ subset, so the
  2026-08-13 cleanup sweep could see "15 open" and had to query SQLite by hand
  to learn which fifteen. A count without an enumeration is a dead end wearing
  a metric's clothes.

  - `claims.sample` now enumerates every open claim, contradicted first, each
    row tagged `contradicted`. `review_limit` raises the cap (max 100) as before.
  - New `claims.open_actionable` — open claims excluding those on deprecated
    memories, which is what `sample` lists. Without it, `open` vs `len(sample)`
    reads as missing rows.
  - New `memory_search(claims="open"|"contradicted")` — the backlog with full
    bodies, composing with `query`, `type`, `namespace`, `tags`, `sort_by`.
  - New `claim_queries.py` holds the read side (backlog query + the shared
    `claim_filter()` predicate). One definition serving the stats block and the
    search filter; also returns `claim_sync.py` (314 lines) under the limit.
  - **Deliberately unchanged:** contradiction detection stays namespace-scoped.
    Bare refs key off the namespace's default repo, so cross-namespace matching
    would pair two different repos' `PR #12`. A real cross-namespace
    contradiction (devex-ai-gateway vs OKREngine, seen during the sweep) stays
    invisible — accepted: a missed one is silent, a fabricated one teaches the
    reader to ignore the metric.

  499 tests passing (13 new in `tests/test_claim_enumeration.py`), ruff + black
  clean.

## Shipped in v0.15.0 (2026-08-13)

Landed on `main` as #45 (squash `b52a095`), released to PyPI on tag `v0.15.0`.
Companion site update shipped as gingugu.com#2 (mobile boot-log fix + content
drift refresh - the site had been advertising a `decay engine` retired in
v0.2.0).

- **Pinned tier + relation-graph metrics.**
  Answers an external review of gingugu that was triaged against the live store
  on 2026-08-13; 4 of its 5 claims held.

  - **Pinned memories** (schema v8, `memories.pinned` + partial index). Ranking
    answers "what is most relevant to this task?" and cannot answer "what must
    never be missing?" — so a governing rule competed for a context slot against
    topical trivia on the same axis. `memory_update(pinned=True)` removes a
    memory from that contest. Pins are **additive to `limit`** (a tier that
    truncates under contention recreates the failure it fixes); bounded by
    `PINNED_HARD_CAP = 20` per namespace, enforced at the write path.
  - **`graph` block in `memory_stats`** (new `graph_stats.py`). Edge count,
    degree, type mix, orphans, and memories stranded past `SPREAD_PER_SEED`.
    Read-only aggregates, no schema change.
  - **Dormancy lifecycle tests.** The 90-day threshold means the wake path had
    never run in production; `tests/test_dormancy_lifecycle.py` forces the
    clock and verifies it end-to-end. **Result: it works** — unproven, not
    broken. Also pins the load-bearing behaviour that a `memory_context` load
    refreshes the dormancy clock, so routinely surfaced memories never go
    dormant and dormancy only ever reaches the tail.
  - **README:** leads with the shipped session protocol, publishes the measured
    retrieval numbers (MRR 0.828 / recall@1 0.611 / recall@5 0.983), and adds an
    **Upgrading** section including the multi-surface install drift gotcha.

  486 tests passing, ruff + black clean.

  **Feeds the two open watch items below.** The `graph` block now measures the
  `related_to` dominance that the blind-spreading-activation item is about:
  measured 2026-08-13 on the live brain, `high_signal_ratio` is **0.392**
  globally (954 of 1,570 edges are `related_to`), **124 orphans** (10.8%), and
  **339 memories carry more edges than `SPREAD_PER_SEED` will ever visit**. That
  last number is the size of the prize for type-weighting the sort.

## Blocked / Pending

- **Retrieval ranking: superseded RESUME notes can outrank current ones —
  WATCH ITEM, do not build, do not bench yet.** The freshness term is inert at
  the timescale resume notes turn over: with `MEMORY_DECAY_LAMBDA = 0.01/day`
  the half-life is ~69 days, so the weighted freshness swing across a whole
  week is **0.0044** against a 0.45 relevance term. The scorer cannot separate
  "written yesterday" from "written last week".

  Observed once, in a session transcript, then **not reproducible** — the
  captured queries were display-truncated and re-running them ranked the
  current note first. An earlier diagnosis blaming `access_count` was wrong and
  has been corrected: it is log-saturated and weighted 0.10, worth at most
  0.078, against an observed 0.278 gap.

  Deliberately parked. `age` (below) does not change ranking, but it makes a
  ranking mistake _visible_ — which is the precondition for benching one
  honestly. Tuning λ against a failure that cannot be replayed is the exact
  error the 2026-07-31 measured-and-rejected result exists to prevent.
  Re-open on a replayable case; capture the verbatim query, the ranked list
  with scores and ages, and the ids. If picked up: bench demoting the targets
  of a `supersedes` relation _before_ touching λ, which is global and would
  flatten `pattern`/`preference` memories that should stay flat.

- **Roll the new startup contract out to installed repos.** The template fix
  below only reaches a repo when `gingugu init --force` runs there. Seven repos
  carry the hook; `keycloakify` and `ogre` are also still on a pre-v0.11.1
  `stop.py`.

- **Spreading activation is blind to `relation_type`.** `dampened_neighbour_ids`
  (`relations.py`) selects neighbours by confidence rank, then _low_ degree, then
  recency, then id — the `SELECT` never fetches `relation_type` at all. So with
  `SPREAD_PER_SEED = 3`, a memory carrying 5 `related_to` edges and 2
  `supersedes` edges can surface three `related_to` neighbours and hide the
  `supersedes` entirely. The low-signal majority actively out-competes the
  high-signal minority on every recall.

  Found 2026-08-04 while writing the relation-discipline guidance. The guidance
  fix only changes what gets written _going forward_; this is the reason the
  existing 943 `related_to` edges still degrade retrieval today. A type-weighted
  term in that sort is a small diff and fixes past and future at once.

  **Gated on benchmark evidence** per `.ai/standards/01-code-and-testing.md` —
  this is a ranking change. Run the fixture floor plus a real-brain pass against
  `bench/local/brain-v1.json` and compare to the hybrid baselines before
  shipping. **Decision already taken:** do _not_ bulk-prune the existing 943
  `related_to` edges. Deleting a third of the graph is destructive and
  irreversible; fix the sort so they stop winning slots instead. Pruning returns
  to the table only if the bench says type-weighting is insufficient.

- **`gingugu ui --host` exposes an unauthenticated full-DB export.** Found
  2026-08-03, still unfiled as an issue.

- **Memory cleanup 3B: content read of the untouched namespaces.** The last
  item in the cleanup arc, and the only one not started. ~540 memories across
  12 namespaces were verified green **by instrument** during steps 1 and 2 and
  never actually read; 3A read every `crow` edge but only memory *titles*, so
  crow's bodies are unread too. Best leads: `devex-on-call-notes` (59 memories,
  worst connectivity in the store, and runbook detail rots silently) and
  `ds-base-images` (both memories still `inferred`). Prioritize with the new
  orphan enumeration — that was the argument for building it first.

- **Over the 300-line rule:** `storage.py` (495), `database.py` (485),
  `handlers/helpers.py` (393). `relations.py` (223) and
  `handlers/relations.py` (224) are off this list as of the orphan/reverse
  work, split into `relation_repair.py` (158) and `handlers/relation_ops.py`
  (125) — splits the feature forced, along seams it wanted anyway. `search.py`
  is now 267 and off this list (the
  engine/`search_common`/`search_filters` split); `claim_sync.py` is 254 and
  off it too, split by the claims-enumeration work into a write path
  (`claim_sync.py`) and a read path (`claim_queries.py`, 125) — a split the
  feature wanted anyway, since both consumers needed the same predicate.

  The pinned-tier work added to three of these rather than fixing them:
  `database.py` +31 (migration 008 and its rationale), `storage.py` +21
  (`count_pinned`, the `pinned` update path), `handlers/helpers.py` +38
  (`_check_pin_budget`, extracted from `handlers/memory.py` specifically to keep
  _that_ file under 300). Splitting them is a separate refactor and was
  deliberately not bundled into a feature change — same call as the
  relation-discipline pass.

  `handlers/helpers.py` went 339 → 355 in the relation-discipline pass: the
  rationale comment on `_RELATION_MIN_SCORE` explains _why_ a similarity-only
  edge is a net loss, which is precisely the knowledge whose absence caused the
  original defect. Splitting the module is a separate refactor and was
  deliberately not bundled into a guidance change. Note the count was already
  stale here (recorded 327, actually 339 at that time).

## Shipped in v0.14.0 (2026-08-13)

Landed on `main` as #39 and #42. PRs #40 and #41 were merged into their
parent branches rather than `main` - see the recovery note at the end of this
section.

- **Relation discipline: quality over volume** (branch `docs/relate-discipline`).
  Reverses every guidance surface that drove relation-writing toward edge count.
  Ranks directional types first (`supersedes`, `contradicts`, `caused_by`,
  `parent_of`/`child_of`); demotes `related_to` to an explicit fallback.

  Touches the `memory_relate` tool description, the `suggested_relations`
  framing on `memory_store`/`memory_update`, `AGENTS.md`, `CLAUDE.md`, this
  repo's `.claude/` copies, and the three `gingugu init` templates. 13 new tests
  in `tests/test_relate_discipline.py` (mutation-verified: they fail when the
  volume vocabulary is reintroduced). No storage, schema, scoring, or response
  shape change.

  **Evidence:** measured on the live 909-memory brain, 69% of 1369 edges were
  `related_to` — a type hybrid search already derives for free. The old
  `AGENTS.md` literally said "most common — use liberally" and "build edges
  aggressively", with a rule of thumb measured in edge count.

  **First step of a 4-item sequence** (order deliberate — eliminate before
  batching, so the batch API gets designed against honest usage numbers):
  1. ← _this_ — stop writing low-value edges
  2. batch `memory_relate` (array of edges, one transaction, return counts not
     echoes; prior art: `portability.py` bulk insert + `relations_imported`)
  3. type-weighted spreading activation (see Blocked / Pending — needs bench
     evidence)
  4. `memory_store` accepts `relations` inline; then a compound session-end tool

- **`gingugu init` now manages the user-level rules file** (branch
  `feature/init-global`, stacked on `docs/relate-discipline`). New
  `bootstrap/global_rules.py` installs and refreshes the memory protocol inside a
  marked block in `~/.claude/CLAUDE.md`.

  **Why:** bootstrap previously only ever wrote under a target _repo_, so the
  user-level file — loaded in **every** session, including directories with no
  project protocol installed — was hand-maintained with no tooling behind it.
  That is exactly why it drifted: it was still saying "build edges aggressively"
  after the repo templates had moved on. Raised as a parking-lot item during the
  relation-discipline work and built the same session.

  **Design, deliberately not a whole-file write.** `init_rules_file` overwrites
  gated on `--force`, which is fine for a file `init` created and owns. The
  user-level file is hand-authored and carries identity/workflow rules unrelated
  to memory, so it gets a marked-section merge instead:
  - missing file → create
  - existing prose, no markers → **append below it**, every prior byte preserved
  - managed block present → replace **only between the markers** (prose before
    and after survives); byte-identical result is a no-op
  - unmanaged memory protocol present → **write nothing**, warn, and explain how
    to opt in (wrap it in the markers). **No flag overrides this**
  - `.bak` only on the refresh path, since appending risks nothing

  **`--force` is deliberately not forwarded to the global step, and this was
  learned the hard way mid-session.** A real `uv run --directory ~/GIT/gingugu
  gingugu init --force` aimed at a repo's hooks appended a duplicate protocol to
  a hand-authored `~/.claude/CLAUDE.md` — the exact outcome the guard existed to
  prevent, delivered by one flag authorizing two decisions of very different
  size. `merge_block` now takes no `force` parameter at all, so the bypass is
  unreachable rather than merely discouraged; a test asserts passing one raises
  `TypeError`, and an end-to-end test asserts `init --force` overwrites repo
  files while leaving a hand-written global file byte-identical.

  **Same run exposed a second footgun:** `--path` defaults to the process's cwd,
  and `uv run --directory X` _moves_ that cwd, so the command bootstrapped the
  gingugu repo instead of the directory it was typed in — `--force`-overwriting
  this repo's own customized hooks (recovered from git). `init` now prints the
  resolved `target` as its first output line. Also fixed: `theme._style_line`
  had no prefix match for the new `appended`/`refreshed` statuses, so they
  rendered without an `[ OK ]` marker.

  **No `--global` flag.** The step is part of the Claude Code bootstrap, like the
  hooks and the `settings.json` merge; making it opt-in would imply the protocol
  is optional. Non-Claude `--client` paths never touch it (their user-level rules
  location is not something this tool should guess at).

  **Hazard closed:** an autouse `sandboxed_global_rules` fixture in
  `tests/conftest.py` redirects the path for the whole suite. Without it every
  test calling `bootstrap.main()` would append to the home directory of whoever
  ran `pytest`. Verified: the real file's checksum is unchanged after a full run.

  19 tests in `tests/test_global_rules.py`.

- **Bootstrap safety fixes found by that same accident** (same branch, separate
  commit). Both are pre-existing bugs in shipped code, not new-feature fallout:

  1. **`--force` destroyed a customized hook with no backup.**
     `_TEMPLATE_SIGNATURE` was the bare word `gingugu`, which every
     gingugu-aware hook contains (the MCP tool names are `mcp__gingugu__*`), so
     `_write_file` classified a heavily customized local `stop.py` as ours and
     overwrote it without a `.bak`. Only a clean git tree saved this repo. Every
     shipped file now carries a `gingugu-init:managed-file` marker.
  2. **The foreign-flag warning was a false positive.** `foreign_flags`
     compared a wired command against a hardcoded list of our template's flags,
     so this repo — whose own `stop.py` genuinely declares `--chat`/`--notify` —
     was told its wiring "was written for a different script". It now reads
     `add_argument` declarations from the script on disk (`declared_flags`),
     falling back to the hardcoded set only when unreadable. It still fires on
     genuinely orphaned flags, which matters because `parse_known_args` means
     those are silently ignored at runtime rather than erroring.

- **The shipped protocol template was enriched from a real hand-tuned global
  file** (user's explicit go-ahead). Added: the credential vault
  (`credential_list` before ever asking for a secret), `memory_forget` for wrong
  information, namespace creation when a repo has none, and a concrete list of
  save triggers instead of a bare "save often". Machine-specific names and
  examples deliberately not carried over.

  446 tests pass, ruff + black clean.

- **`age` reports maintenance, and the freshness anchor stops discarding edits**
  (branch `fix/age-freshness-anchor`, stacked on `feature/init-global`). Found
  by soak-testing the three commits above and watching the session-start payload:
  a memory substantially rewritten ~20 minutes earlier surfaced as
  `"age": "7 weeks ago"`.

  Three nested defects, shipped together because #1 alone would make `age`
  _look_ right while ranking stayed wrong:

  1. **Display.** `age` came off raw `created_at`, ignoring
     `decay.reference_timestamp` — the anchor the scorer, the spread-neighbour
     sort and staleness all already used. `age` was the only consumer that
     disagreed with the other three. Now anchored, and elaborated to
     `"7 weeks ago (updated just now)"` where the two differ (~4 tokens, only on
     maintained memories — "durable AND current" beats either half alone).
  2. **Ranking.** `reference_timestamp` was documented as
     `COALESCE(last_confirmed, updated_at, created_at)`, and COALESCE takes the
     first non-null, not the max. A content edit made after the last
     confirmation was discarded. Now a `MAX` in Python and in both SQL call
     sites (`relations.py`, `stats.py`); only `last_confirmed` needs a null
     guard, the other two columns are `NOT NULL`.
  3. **Root cause.** `storage.update` bumped `last_confirmed` only when
     `confidence=VERIFIED` was passed explicitly, so ordinary content
     maintenance never registered as a confirmation. Now a title/content change
     confirms, reusing the "did the matching surface move?" test already in the
     module. Retypes, tag edits and metadata writes deliberately do not.

  **Accepted caveat, surfaced before building:** confirming on rewrite also
  suppresses `review_hints` and `suggests_deprecation`, so a one-word typo fix
  resets the staleness clock. Correct when you genuinely restated the claim,
  not free.

  **Bench gate cleared** (#2 and #3 touch ranking, per
  `.ai/standards/01-code-and-testing.md`): fixture floor 1.000 MRR, and a real-
  brain A/B on the same DB — before vs after — came back **identical on every
  metric and every per-question score**, tokens −0.2%. Note the stored
  `bench/local/*.json` baselines are no longer valid reference points on their
  own: the live brain has grown ~9 days since they were captured, which alone
  moves recall@5 and token counts. A/B on one DB is the only honest comparison.

  456 tests pass (10 new), ruff + black clean.

- **Recovery: #40 and #41 never reached `main`.** The three PRs were a stack,
  merged bottom-up but without retargeting the children to `main` first, so
  only #39 landed there. `main` was left missing 29 files / +1199 lines. A
  plain merge produced four phantom conflicts - artifacts of #39 being
  *squash*-merged, so identical content carried different SHAs. Rebasing
  `--onto origin/main` past the squashed commit resolved it with zero
  conflicts and a byte-identical tree, landed as #42.

  **Rule for the next stack:** merging bottom-up is necessary but not
  sufficient - retarget each child to `main` (`gh pr edit <child> --base
  main`) *before* merging its parent, and verify with `git log --oneline
  origin/main..origin/<branch>` before deleting anything. "Merged" says
  nothing about *which* branch it merged into.

## Shipped in v0.13.0 (2026-08-04)

- **`age` derived into every memory payload** — `decay.relative_age()` returns
  a human-readable interval (`"2 days ago"`) computed at serialization from
  `created_at`, wired into both `_memory_summary` and `_compact_summary`.

  The session protocol mandates `compact=true` at session start, and compact
  drops all timestamps by design — so at the one moment temporal context
  matters most, reading the RESUME memory, the agent could not tell last
  night's note from June's. Raw ISO timestamps already ship in full mode and
  still get misread, because the arithmetic is done unreliably or skipped;
  deriving the interval removes it. ~4 tokens per memory.

  **Never persisted.** Same lifecycle as `score` and
  `credentials.expiry_status`. A stored `"6 days ago"` rots the moment the
  world moves — the bug class `memory_claims` and `review_hints` exist to
  catch. Two tests pin the contract: the same instant must read `"1 day ago"`
  then `"1 week ago"` as `now` advances, and `age` must never appear in a
  `memory_export` payload.

  Placed in `decay.py`, not `helpers.py`, because the latter is already 327
  lines and over the rule.

  Verified live after an MCP restart: the first compact context load carried
  `age` on every memory and immediately flagged one titled "PR #12 open" as
  three weeks old. It also disambiguated two same-day RESUME notes whose titles
  mislead — "3rd sail" (10 hours) is _newer_ than "end of day" (21 hours).

- **Startup contract no longer asks the agent to infer the workspace** — the
  contract said "Append any other workspace repos to the list", but a
  SessionStart hook receives exactly one directory. Verified against 57 logged
  payloads: the key set is `cwd`, `hook_event_name`, `session_id`, `source`,
  `transcript_path`. No workspace roster exists in it.

  The agent reached for the only workspace-shaped list in its context,
  "Additional working directories" — a permission allowlist — and loaded five
  namespaces at startup instead of two. Two of those paths were subdirectories
  of one repo, one was `~/.claude`, one was `/tmp`.

  Now states a floor and a rule: `crow` + the `cwd` repo always, other
  namespaces only on demand. A config file or env var listing sibling
  namespaces was considered and **rejected** — a hand-maintained list is the
  same guess written somewhere worse.

## Shipped in v0.12.0 (2026-08-01)

- **Compact write-time hints (PR #35, merged `a8439e4`)** — a payload
  bug, found by reading a `/sink-the-ship` transcript. `memory_store`'s
  `similar_memories` / `suggested_relations` and `memory_update`'s
  `suggested_relations` returned each candidate's **full body**, so one store
  could attach six complete memories to its response. Measured on the live
  821-memory corpus (median body 1,891 chars): ~11,300 characters, ~2,800
  tokens, charged to the caller on every write — routinely more than the
  memory being saved, and never asked for.

  `_compact_summary` already existed for `compact` reads; both hint paths in
  `handlers/helpers.py` simply called `_memory_summary` instead. Hints are now
  always compact, with no flag to inflate them — a hint is a pointer, and
  `memory_recall` fetches the body when a candidate matters. ~89% smaller. The
  `memory` object in the same response still returns in full; only the
  unsolicited extras were trimmed.

  398 tests (+5), ruff + black clean. The new tests were verified to bite by
  reverting the two call sites (4 of 5 fail). Relation-hint coverage is unit
  level with mocked scores, matching `test_suggest_relations.py` — real hybrid
  scores aren't deterministic enough to pin a positive hit at the tool surface.

- **CI hang guards + offline test suite (PR #36, merged `a8e1bea`)** — found
  when one matrix cell (ubuntu-latest / 3.12) sat in `Pytest` for 10+ minutes
  on PR #35 while the other eight finished in 25s–1m23s. Same code, same
  command; 3.12 passed on macOS and Windows, so it was never version-specific.

  Root cause: `embeddings_enabled` defaults to **True** and the fastembed
  backend lazy-loads an ~80MB ONNX model from HuggingFace on first encode
  (`embeddings.py:117-120`), with **no timeout at any layer** — not the
  download, not the pytest step, not the job. `tests/conftest.py` said nothing
  about embeddings, so ~35 test files were doing a live network fetch on a cold
  CI cache. One stalled fetch would have run to GitHub's 6-hour default.

  Three guards, defence in depth:
  1. `offline_embeddings` autouse fixture in `conftest.py`, sibling to the
     existing `fake_keyring`. No test _wanted_ the real model — `test_embeddings`
     says real loads aren't exercised, `test_true_hybrid` injects its own
     deterministic embedder, and four files had already been patching this env
     var one at a time. Now it's the default.
  2. `timeout = 60` via `pytest-timeout` (new dev dep, 2.4.0 verified on PyPI) —
     a hung test fails as a test. Proven to abort a real hang.
  3. `timeout-minutes: 15` on the CI job as the outer guard.

  Suite: 393 pass in **2.53s, down from 6.58s** — the real model was being
  loaded and cost 60% of runtime while nothing asserted on it.

## Shipped in earlier releases

- **Hook arg robustness + `default_repo` actually applying (v0.11.1)** — three
  shipped defects.

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

- **Claim-extraction precision (v0.11.0)** —
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
  `memory_store` / `memory_update` (link vs merge candidates), compact payload.
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
