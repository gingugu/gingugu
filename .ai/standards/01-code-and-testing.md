# Standards: Code & Testing

## Code

- **Python `>=3.11`**, PEP 8, type hints required on all public functions.
- **`ruff` + `black`** clean before every commit (`uv run ruff check . && uv run black .`).
- **300-line file limit** per module — split early into helpers/submodules. One
  responsibility per file.
- **Simplicity over cleverness** — no premature abstraction.
- **In a mixin, declare what you BORROW under `TYPE_CHECKING`.** Only what the
  mixin *provides* gets a runtime body. A stub written to document a dependency
  (`def _commit(self): raise NotImplementedError`) sits ahead of the real base
  in the MRO and shadows the implementation it was describing - which is how
  `DerivedTables` briefly broke every write path in `MemoryStore`. Bare
  attribute annotations are already safe, but keeping both inside one
  `if TYPE_CHECKING:` block beats remembering which is which. If you cannot say
  whether a member is borrowed or provided, the mixin boundary is wrong.
- **A new subpackage must be proven to ship.** Adding a directory under
  `src/gingugu/` is invisible to the test suite: tests import from the source
  tree, so a packaging miss passes everything and breaks only for pip users.
  Build and look: `uv build --wheel -o <tmp>` then `unzip -l <tmp>/*.whl`.
  hatchling recurses `packages = ["src/gingugu"]`, so the answer is normally
  yes; "normally yes" is not a measurement.
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
- **The suite is offline and bounded.** Two autouse fixtures in
  `tests/conftest.py` enforce it: `fake_keyring` keeps tests off the OS
  keychain, `offline_embeddings` keeps them off the network (the fastembed
  backend otherwise downloads an ~80MB model on a cold cache, untimed). A test
  needing a real external dependency must opt in explicitly. `timeout = 60`
  (pytest-timeout) makes a hung test fail as a test rather than as a stalled
  CI job, and CI carries `timeout-minutes: 15` as the outer guard.
- **Unit tests** for storage, search, relations, context, decay, consolidation.
- **Integration tests** for end-to-end MCP flows (store → recall → context;
  store → relate → recall include_related).
- Run `uv run pytest -v` green before opening a PR.
- CI matrix: ubuntu/macos/windows × Python 3.11–3.13 — cross-platform claims must
  be backed by green CI on all three OSes, not just local.
- **A test asserting current behavior is not evidence the behavior is right.**
  `test_reforce_over_our_own_file_makes_no_backup` asserted that `--force` wrote
  no backup over our own managed file, and stayed green across every release in
  which that behavior was destroying users' local edits. The suite was pinning
  the defect, so a passing run said nothing about that path. When a test named
  after a *mechanism* ("no backup is written") turns red, ask what the user
  needed before assuming the change broke it - and when a bug is fixed, invert
  the test that encoded it rather than deleting it, so the record shows the
  behavior was chosen and then rejected.
- **A filter's test must assert on the set the filter produces, not on what
  survives everything downstream.** The first version of the involuntary-recall
  exclusion tests asked whether a pinned or superseded memory appeared in the
  *injected* output. Both stayed green with the exclusions deleted from the SQL,
  because the score gates happened to reject those rows anyway. Re-pointed at
  the sweep's candidate set - the thing the `WHERE` clause actually promises -
  both turn red the moment the clause goes. Pair the negative assertion with a
  positive one (`target in swept`) so a query broken into returning nothing
  cannot pass as a working filter.
- **Prove a guard is load-bearing by removing it.** `git stash push <file>`, or
  a scripted edit-run-restore, and confirm the test fails without the code it
  covers. Two mechanisms in this repo were verified that way and one test was
  found to be vacuous by exactly this check; a test that passes before *and*
  after is documentation, not coverage.
- **Real-model tests are marked `bench_embeddings`,** excluded from the matrix
  (`-m "not bench_embeddings"`) and run once in their own CI job with a cached
  model. That job is the only sanctioned exception to `offline_embeddings`. Use
  it when a test must prove that the *pipeline* works and not merely that the
  arithmetic does: hand-scored candidates cannot catch broken SQL, an inverted
  cosine, or a filter that stopped applying. Keep the fixture corpus synthetic
  and in-repo - a real brain and a real prompt log belong in the gitignored
  `bench/local/` and `logs/`, never in a public repository.
- **Ranking/scoring changes ship with benchmark evidence:** run
  `uv run python -m bench` (fixture floor) and a real-brain run against the
  recorded baseline (see `docs/roadmap.md` Phase 5.75). Grading is
  deterministic math only — never LLM-as-judge (design law, 2026-07-18).
- **Know what the bench cannot see, and say so.** The default fixture run
  reports `retrieval: bm25-only`, so it is structurally blind to any change in
  the semantic cohort, the entry threshold, or the fusion of the two - a green
  fixture run is evidence about BM25 and about nothing else (pass
  `--embeddings` for a hybrid pass). Real defects have lived in that blind
  spot. When a change lands in one, measure it directly against a copy of a
  real brain and report that, rather than quoting a benchmark that never
  exercised the code.
- **Confirm the harness executes the changed function before quoting a
  delta.** A gate recorded by a past session is not self-validating. The
  type-weighted spreading activation item (2026-08-27) carried an explicit
  instruction to bench against `bench/local/brain-v1.json` and compare to the
  hybrid baselines - but `run_benchmark` calls `search()`, and the changed
  function was reachable only from `handlers/helpers.py`. Both arms would have
  printed identical numbers and read as a clean pass. Trace the call path
  first; if the harness cannot reach the code, extending it is part of the
  work, not scope creep around it. `bench --spread` exists for exactly this
  path, and the fixture dataset carries a `relations` block so the CI floor
  exercises it at all.
- **A metric must not read the knob the change turns.** In that same item, the
  first A/B simulated "before" by zeroing `RELATION_WEIGHT` while the new
  metric derived "high signal" from that same table - producing a flawless
  0.0% -> 73.0% that was pure artifact. Define the measurement against
  something the intervention cannot touch (there: `RelationType` itself). An
  implausibly clean delta is a reason to audit the instrument, not to
  celebrate. Honest figure once fixed: 67.7% -> 73.0%.
- **Stamp each A/B arm with proof of which code ran.** Swapping a file between
  runs can land inside Python's one-second `.pyc` mtime granularity, and if the
  two variants are the same size the stale bytecode is silently reused - the
  file on disk and the running process disagree. Clear `__pycache__` between
  arms and print the loaded value from inside the process as the run's first
  line.
- **Reintroduce the defect to prove the test is a guard.** A test written to
  prevent a recurrence is not finished until it has been seen to fail against
  the old behaviour. Twice this has caught a test that proved nothing: a
  limit-invariance suite passed against the broken code because its corpus was
  smaller than the pool being truncated, so no truncation ever occurred. Revert
  the fix, watch it go red, restore it. Cheap, and the alternative is a green
  suite that guards nothing - which is exactly how the `--force` backup defect
  survived several releases.
- **A test that legitimately passes against the old code is a characterization
  test - label it.** Some tests in a fix's suite pin behaviour the fix
  introduces rather than a defect it removes, and those cannot go red on the old
  code. That is fine, but it must be written down in the test itself, otherwise
  the next reader counts it among the guards and the suite looks stronger than
  it is. Say which ones bite and which ones describe.

## Docs in lockstep

- Update `CHANGELOG.md` (`[Unreleased]`) for every user-visible change.
- Keep `README.md` and `docs/architecture.md` mermaids in sync with the tool surface.
- Update `.ai/` per the enforcement table before every commit/PR.
