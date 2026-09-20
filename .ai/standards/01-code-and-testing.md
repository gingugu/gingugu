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
- **Token and dollar cost is a TIEBREAKER, never an objective.** Among designs
  equal on functionality and performance, take the cheaper one. A design that
  is cheaper *and* better is free money - take it. A design that is cheaper but
  worse on any axis (accuracy, retrieval quality, comprehension, portability,
  correctness) is rejected, and is not presented as a trade-off worth weighing.
  Cost gets no vote on what the thing does or how well it does it; it only
  breaks ties.

  The surfaces where this pays are the ones charged on *every* session
  unconditionally: the pinned tier (loaded ahead of ranking), `compact`
  payloads at session start (the protocol mandates them), and write-time hints
  (unasked-for extras on every write - already compacted for this reason in
  #35).

  Worked example of taking it: authoring a memory's short summary rather than
  head-truncating it is more signal *and* fewer tokens, sacrificing nothing.
  Worked example of refusing it: storing memory content in a denser or bespoke
  encoding would buy tokens by paying in comprehension accuracy and
  cross-model portability, so it stays plain English.

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
- **A corpus with no instance of the shape is blind to it, and growing the
  corpus does not help (2026-09-20).** The fixture reached 34 memories and 20
  questions, and was still structurally incapable of seeing the top board item:
  every question was a single-answer topical lookup and the corpus held no
  template family at all. It had near-miss distractors sharing *vocabulary*,
  which is a different instrument from siblings sharing *scaffolding*. A fix
  built against it would have gone green whether or not it worked. The earlier
  diagnosis, "an 8-query fixture cannot see this", was right about the blindness
  and wrong about the cause: it was never about question COUNT. **Before
  trusting a benchmark to grade a defect, confirm the corpus contains an
  instance of that defect - and if it does not, adding one is step one of the
  work.** The corollary is that the new case must FAIL on the unfixed code, and
  be pinned that way: `xfail(strict=True)` is the shape, because it fails the
  suite the moment the fix lands and forces someone to come update it.
- **Control the confounds in a labelled cohort, and pin the control.** The first
  draft of that same template family made the older members longer, which is
  what real ones do. BM25 length normalization then decided every query on its
  own, and the probe "passed" while measuring nothing but document length.
  Equalizing length turned it into a real instrument, and
  `test_handoff_family_members_are_equal_length` exists so a later well-meaning
  edit cannot quietly reintroduce the confound. A fixture is measuring
  apparatus: the variable under test must be the only one that moves.
- **A fixture clean enough to isolate a variable can be too clean to predict
  anything (2026-09-20).** That same length-equalized cohort then rated a
  retrieval change at mrr 1.0000, recall@1 0.9318 and zero regressions. On the
  real brain the same change **halved recall@1**. Equalizing length removed the
  confound *and* removed the condition that makes the real corpus hard. A
  fixture can therefore prove a defect EXISTS while being worthless for sizing a
  fix - keep the two jobs separate, and never let a green fixture stand in for a
  real-brain measurement on a ranking change.
- **Count what one question is worth before believing a delta (2026-09-20).**
  `bench/local/brain-v1.json` has 30 binary questions, so one question is 0.033
  and a "+0.050 recall@1" result is one and a half questions. Two separate
  ranking changes looked like clear improvements on it and were killed by a
  finer instrument; one of them was a *strict Pareto improvement* there and cut
  recall@1 in half on 135 questions. Treat a delta smaller than a few questions
  as noise, and read non-monotonic sweep rows as the harness reporting its own
  error bar.
- **Generate labels instead of judging them when the property is checkable
  (2026-09-20).** Hand-labelling is what capped that set at 30. `bench/probes.py`
  gets 135 questions at higher trust by choosing a property a `SELECT` can
  prove: a phrase occurring in exactly one memory of a namespace has exactly one
  correct answer. Nobody adjudicates, every candidate is re-verified against the
  corpus before it is emitted, and `tests/test_probes.py` asserts that property
  holds for every generated question - because a large set of confident guesses
  is worse than a small set of honest ones. When a labelling task is blocking a
  measurement, look first for a label that can be verified rather than decided.
- **The same blind spot covers types, not just behaviour - second confirmed
  instance (2026-09-02).** Because `offline_embeddings` is autouse, no test had
  ever called `embeddings.cosine` with real encoder output. fastembed returns a
  Python list of `numpy.float32`, so `cosine` had been returning a `float32`
  while declaring `-> float`, and `handlers/hints.py` had been emitting
  `"similarity": "1.0"` as a **string** on every real-embeddings hint. It
  surfaced only when a new caller wrote the value through `json.dumps`, which
  refuses that type; the MCP serializer had been quietly stringifying it for
  weeks. **A serializer that coerces instead of raising turns a type defect
  into a data defect.** When a numeric field crosses a serializer, assert its
  `type` somewhere and not only its value - and when a fixture disables a
  dependency wholesale, the dependency's *return types* are unexercised too.
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
- **Probing a live brain: assert the sandbox, never just print it.** Every
  config variable in this repo is `MEMORY_*`, not `GINGUGU_*` - the rename left
  the env surface behind. `os.environ.get` on a name that does not exist returns
  `None` and falls through to the real database, so a wrong prefix and no
  prefix at all are indistinguishable at runtime. A one-off script that meant to
  read a scratch copy therefore ran against the live store, and it had printed
  the path it was using one line above the number being read.

  Printing is not checking. Assert:

  ```python
  cfg = load_config()
  assert str(cfg.db_path) == str(expected), f"NOT SANDBOXED: {cfg.db_path}"
  ```

  Cheaper still for a read-only look: `sqlite3 "file:$DB?mode=ro"` against a
  `.backup` copy, which cannot write to the original whatever the config says.
  Reach for `load_config()` only when the code path itself is what is under
  test, and assert the path first.
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
