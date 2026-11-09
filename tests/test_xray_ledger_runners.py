"""X11 for every language X-ray parses: vitest, jest and ``go test`` runs are scored too.

The X-ray law: the post-edit note predicts tests for a ``.ts`` or ``.go`` edit,
so the ledger has to see those tests fail, or the score only ever measures
Python. A run is recognised by its command; failed test files are read from
the runner's own summary lines, colour codes stripped.

Runners print paths relative to where they ran, not to the repo: vitest from
the package (``src/a.test.ts`` for ``ui/src/a.test.ts``), ``go test`` as an
import path plus a file name (``example.com/app/store`` + ``store_test.go``).
A failed path and a predicted one therefore match when they are equal or one
ends with the other on a ``/`` boundary. Only a test file's name is ever kept
from output - never a directory from a stack trace.
"""

from __future__ import annotations

import pytest

from gingugu.xray import ledger, local
from tests.test_xray_edit_after import CALLS
from tests.test_xray_edit_hook import _repo
from tests.test_xray_ledger import _bash


@pytest.fixture
def state(tmp_path):
    return tmp_path / "data" / "hook-sessions"


@pytest.fixture
def calls(tmp_path):
    return _repo(tmp_path, CALLS)


@pytest.mark.parametrize(
    ("command", "is_run"),
    [
        ("npx vitest run", True),
        ("vitest --run src/a.test.ts", True),
        ("cd ui && npx vitest run", True),
        ("pnpm vitest", True),
        ("npx jest src/", True),
        ("yarn jest --ci", True),
        ("node_modules/.bin/jest", True),
        ("npm test", True),
        ("npm run test -- --run", True),
        ("pnpm test", True),
        ("yarn test", True),
        ("bun test", True),
        ("go test ./...", True),
        ("go test -run TestSave ./store", True),
        ("CGO_ENABLED=0 go test ./...", True),
        ("gotestsum -- ./...", True),
        ("grep -rn vitest package.json", False),
        ("cat jest.config.js", False),
        ("go vet ./...", False),
        ("go build ./...", False),
        ("npm install", False),
        ("ls go-test", False),
    ],
)
def test_every_runner_is_recognised_by_its_command(command, is_run):
    assert ledger.is_test_run(command) is is_run


ESC = "\x1b"
VITEST = (
    f" {ESC}[31m❯{ESC}[39m src/a.test.ts (2 tests | 1 failed) 5ms\n"
    "   × adds\n"
    " ✓ src/b.test.ts (1 test) 2ms\n\n"
    "⎯⎯⎯⎯⎯⎯⎯ Failed Tests 2 ⎯⎯⎯⎯⎯⎯⎯\n\n"
    f"{ESC}[41m{ESC}[1m FAIL {ESC}[22m{ESC}[49m src/a.test.ts > math > adds\n"
    "AssertionError: expected 3 to be 4\n"
    f" FAIL  |web| src/views/c.spec.tsx > renders\n"
    "Error: boom\n"
)
JEST = (
    " PASS  src/ok.test.js\n"
    f" {ESC}[1m{ESC}[41m FAIL {ESC}[49m{ESC}[22m src/sum.test.js\n"
    "  ● adds 1 + 2 to equal 3\n"
    " FAIL  __tests__/user.test.tsx\n"
    "Tests:       2 failed, 1 passed, 3 total\n"
)
GO = (
    "--- FAIL: TestSave (0.00s)\n"
    "    store_test.go:12: got 1 want 2\n"
    "FAIL\n"
    "FAIL\texample.com/app/store\t0.003s\n"
    "ok  \texample.com/app/api\t0.002s\n"
    "# example.com/app/web [example.com/app/web.test]\n"
    "web/web_test.go:5:2: undefined: Nope\n"
    "FAIL\texample.com/app/web [build failed]\n"
    "--- FAIL: TestRun (0.00s)\n"
    "panic: boom [recovered]\n"
    "\t/Users/someone/src/app/cli/run_test.go:20 +0x1d\n"
    "\t/Users/someone/src/app/cli/run.go:9 +0x2a\n"
    "FAIL\texample.com/app/cli\t0.010s\n"
    "FAIL\n"
)


def test_vitest_failures_are_read_from_its_fail_lines():
    assert ledger.failed_tests(VITEST) == ["src/a.test.ts", "src/views/c.spec.tsx"]


def test_jest_failures_are_read_from_its_fail_lines():
    assert ledger.failed_tests(JEST) == ["__tests__/user.test.tsx", "src/sum.test.js"]


def test_go_failures_are_the_failing_packages_test_files():
    assert ledger.failed_tests(GO) == [
        "example.com/app/cli/run_test.go",
        "example.com/app/store/store_test.go",
        "example.com/app/web/web_test.go",
    ]


def test_go_test_json_output_is_read_too():
    out = (
        '{"Action":"output","Package":"example.com/app/store","Test":"TestSave",'
        '"Output":"    store_test.go:12: got 1 want 2\\n"}\n'
        '{"Action":"fail","Package":"example.com/app/store","Test":"TestSave"}\n'
        '{"Action":"fail","Package":"example.com/app/store"}\n'
        '{"Action":"pass","Package":"example.com/app/api"}\n'
    )
    assert ledger.failed_tests(out) == ["example.com/app/store/store_test.go"]


def test_a_stack_trace_never_puts_a_directory_on_disk(calls, state):
    ledger.observe(_bash(calls, "go test ./...", stdout=GO), state)
    text = ledger.path(state).read_text()
    assert "/Users/someone" not in text and "run.go" not in text.replace("run_test.go", "")


@pytest.mark.parametrize(
    "out",
    [
        " FAIL  /Users/someone/app/src/a.test.ts\n",  # vitest/jest, a file outside the root
        " FAIL  ../../other/src/a.test.ts > t\n",
        "FAILED /home/someone/app/tests/test_a.py::t - x\n",  # pytest, another rootdir
        "FAILED ../app/tests/test_a.py::t - x\n",
        "    a_test.go:3: x\nFAIL\t_/Users/someone/proj/pkg\t0.1s\n",  # go, outside a module
        f" FAIL  src/{'a' * 600}.test.ts\n",
    ],
)
def test_a_path_that_is_absolute_climbs_out_or_runs_long_is_never_kept(out):
    assert ledger.failed_tests(out) == []


def test_passing_runs_have_no_failures():
    assert ledger.failed_tests(" PASS  src/ok.test.js\n ✓ src/b.test.ts (1 test)\n") == []
    assert ledger.failed_tests("ok  \texample.com/app/api\t0.002s\n") == []


def test_a_fail_line_naming_no_script_file_records_nothing():
    assert ledger.failed_tests(" FAIL  some words here\nFAIL\n") == []


# --- scoring across path forms ---------------------------------------------------------


def _predict(calls, state, tests, session="cc-1"):
    root = local.root(str(calls))
    ledger.predict(state, session, root, "src/x.ts", "calls", tests)
    ledger.predict(state, session, root, "src/x.ts", "imports", [])


def test_a_runner_path_relative_to_a_package_matches_the_repo_path(calls, state):
    _predict(calls, state, ["ui/src/a.test.ts"])
    ledger.observe(_bash(calls, "npx vitest run", stdout=VITEST), state)
    s = ledger.score(state)
    assert s["by_kind"]["calls"]["top5"] == 1  # src/a.test.ts
    assert s["by_kind"]["calls"]["missed"] == 1  # src/views/c.spec.tsx


def test_a_go_import_path_failure_matches_the_repo_path(calls, state):
    _predict(calls, state, ["store/store_test.go", "cli/run_test.go"])
    ledger.observe(_bash(calls, "go test ./...", stdout=GO), state)
    s = ledger.score(state)
    assert s["by_kind"]["calls"] == {"top5": 2, "listed": 0, "missed": 1}


def test_a_match_is_on_a_path_boundary_only(calls, state):
    _predict(calls, state, ["ui/xsrc/a.test.ts", "notstore/store_test.go"])
    ledger.observe(_bash(calls, "npx vitest run", stdout=" FAIL  src/a.test.ts > t\n"), state)
    ledger.observe(_bash(calls, "go test ./...", stdout=GO), state)
    assert ledger.score(state)["by_kind"]["calls"]["top5"] == 0
