"""X11, lean: X-ray scores its own test predictions against the tests that actually fail.

Every time the post-edit note names tests for an edited file, two predictions go
into a local ledger: the tests nearest the edited definitions by call
(``calls``) and the tests nearest the file by import (``imports``). When a
``pytest`` run in that session prints failures, the failed test files are
recorded beside them. ``gingugu xray score`` then says, per predictor, how many
failed test files were in its first five, anywhere in its list, or missed - so
whether X-ray helps is measured, not asserted, and the two predictors can be
compared on real work.

Local only: the ledger sits beside the hook state (``xray-ledger.jsonl``,
owner-only), holds repo-relative paths and hashed session and repo ids, and
never reaches the brain.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from gingugu.xray import ledger
from tests.test_xray_edit_after import CALLS, _after, _edit, _read
from tests.test_xray_edit_hook import _repo


@pytest.fixture
def state(tmp_path):
    return tmp_path / "data" / "hook-sessions"


@pytest.fixture
def calls(tmp_path):
    return _repo(tmp_path, CALLS)


def _bash(repo, command, stdout="", stderr="", session="cc-1", interrupted=False):
    return {
        "session_id": session,
        "cwd": str(repo),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {
            "stdout": stdout,
            "stderr": stderr,
            "interrupted": interrupted,
            "isImage": False,
        },
    }


PYTEST_FAIL = (
    "....F.\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_svc.py::test_serve - AssertionError: assert 1 == 2\n"
    "FAILED tests/test_svc.py::test_other - KeyError\n"
    "ERROR tests\\test_cli.py::test_main - ImportError\n"
    "2 failed, 3 passed, 1 error in 0.12s\n"
)


# --- reading pytest output ---------------------------------------------------------


def test_failed_test_files_are_read_from_the_short_summary():
    assert ledger.failed_tests(PYTEST_FAIL) == ["tests/test_cli.py", "tests/test_svc.py"]


def test_a_passing_run_has_no_failures():
    assert ledger.failed_tests("6 passed in 0.10s\n") == []


@pytest.mark.parametrize(
    ("command", "is_run"),
    [
        ("uv run pytest -n auto -q", True),
        ("python -m pytest tests/test_x.py", True),
        ("env -u VIRTUAL_ENV uv run pytest tests/", True),
        ("grep -rn pytest pyproject.toml", False),
        ("cat pytest.ini", False),
        ("git log", False),
    ],
)
def test_a_test_run_is_recognised_by_its_command(command, is_run):
    assert ledger.is_test_run(command) is is_run


# --- the ledger ---------------------------------------------------------------------


def test_the_post_edit_note_records_both_predictors(calls, state):
    read = _read(calls, "pkg/core.py")
    from gingugu.xray import read_hook

    read_hook.check(read, state)
    _edit(calls, "pkg/core.py", "    return 1", "    return 2", state)
    rows = [json.loads(ln) for ln in ledger.path(state).read_text().splitlines()]
    kinds = {r["kind"]: r for r in rows if r["t"] == "p"}
    assert set(kinds) == {"calls", "imports"}
    assert kinds["calls"]["tests"] == ["tests/test_core.py", "tests/test_svc.py"]
    assert kinds["calls"]["file"] == "pkg/core.py"
    assert "cc-1" not in ledger.path(state).read_text()  # session ids are hashed
    assert str(calls) not in ledger.path(state).read_text()  # no absolute paths


def test_the_ledger_is_owner_only(calls, state):
    _edit(calls, "pkg/core.py", "    return 1", "    return 2", state)
    assert ledger.path(state).stat().st_mode & 0o777 == 0o600


def test_a_test_run_records_its_failures(calls, state):
    ledger.observe(_bash(calls, "uv run pytest -q", stdout=PYTEST_FAIL), state)
    rows = [json.loads(ln) for ln in ledger.path(state).read_text().splitlines()]
    assert rows[-1]["t"] == "o"
    assert rows[-1]["failed"] == ["tests/test_cli.py", "tests/test_svc.py"]


def test_failures_on_stderr_count_too(calls, state):
    ledger.observe(_bash(calls, "uv run pytest", stderr=PYTEST_FAIL), state)
    assert "tests/test_svc.py" in ledger.path(state).read_text()


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Bash", "tool_input": {"command": "git status"}},
        {"tool_name": "Read", "tool_input": {"command": "pytest"}},
        {"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": "x"},
        {"tool_name": "Bash", "tool_input": "pytest"},
        {},
    ],
)
def test_anything_but_a_finished_test_run_records_nothing(payload, state, calls):
    payload = {"session_id": "cc-1", "cwd": str(calls), **payload}
    ledger.observe(payload, state)
    assert not ledger.path(state).exists()


def test_an_interrupted_run_records_nothing(calls, state):
    ledger.observe(_bash(calls, "pytest", stdout=PYTEST_FAIL, interrupted=True), state)
    assert not ledger.path(state).exists()


def test_the_ledger_rotates_past_its_cap(calls, state, monkeypatch):
    monkeypatch.setattr(ledger, "MAX_BYTES", 200)
    for _ in range(10):
        ledger.observe(_bash(calls, "pytest", stdout=PYTEST_FAIL), state)
    assert ledger.path(state).stat().st_size <= 200 + 300
    assert ledger.path(state).with_name("xray-ledger.jsonl.1").exists()


def test_a_damaged_ledger_line_is_skipped_by_the_score(calls, state):
    ledger.path(state).parent.mkdir(parents=True, exist_ok=True)
    ledger.path(state).write_text('{"t": "o"\nnot json\n[1, 2]\n')
    assert ledger.score(state)["runs"] == 0


# --- the score ------------------------------------------------------------------------


def _session(calls, state, failures: str, session="cc-1"):
    from gingugu.xray import read_hook

    read_hook.check(_read(calls, "pkg/core.py", session=session), state)
    p = _after(calls, "pkg/core.py", session=session, old_string="x", new_string="    return 1")
    from gingugu.xray import edit_note

    edit_note.check(p, state)
    ledger.observe(_bash(calls, "pytest", stdout=failures, session=session), state)


def test_the_score_buckets_each_failed_test_file_per_predictor(calls, state):
    _session(calls, state, "FAILED tests/test_svc.py::test_serve - x\n1 failed\n")
    s = ledger.score(state)
    assert s["runs"] == 1 and s["failing_runs"] == 1 and s["failed"] == 1
    assert s["by_kind"]["calls"] == {"top5": 1, "listed": 0, "missed": 0}
    assert s["unpredicted"] == 0


def test_a_failure_no_prediction_named_is_a_miss(calls, state):
    _session(calls, state, "FAILED tests/test_unrelated.py::test_x - x\n")
    s = ledger.score(state)
    assert s["by_kind"]["calls"]["missed"] == 1
    assert s["misses"] == [{"test": "tests/test_unrelated.py", "edited": ["pkg/core.py"]}]


def test_a_failure_in_a_session_with_no_edit_note_is_unpredicted(calls, state):
    ledger.observe(_bash(calls, "pytest", stdout="FAILED tests/test_svc.py::t - x\n"), state)
    s = ledger.score(state)
    assert s["unpredicted"] == 1
    assert s["by_kind"]["calls"] == {"top5": 0, "listed": 0, "missed": 0}


def test_predictions_only_count_for_their_own_session(calls, state):
    _session(calls, state, "", session="cc-1")
    ledger.observe(
        _bash(calls, "pytest", stdout="FAILED tests/test_svc.py::t - x\n", session="cc-2"),
        state,
    )
    assert ledger.score(state)["unpredicted"] == 1


def test_a_passing_run_is_counted_but_scores_nothing(calls, state):
    _session(calls, state, "5 passed\n")
    s = ledger.score(state)
    assert s["runs"] == 1 and s["failing_runs"] == 0 and s["failed"] == 0


def test_an_empty_ledger_scores_zero(state):
    s = ledger.score(state)
    assert s["runs"] == 0 and s["failed"] == 0


def test_the_score_renders_as_one_short_text(calls, state):
    _session(calls, state, "FAILED tests/test_svc.py::test_serve - x\n")
    text = ledger.render(ledger.score(state))
    assert text.startswith("X-RAY SCORE: 1 test run, 1 failing (1 failed test file).")
    assert "calls: 1 in top 5" in text and "imports:" in text


def test_the_cli_prints_the_score(calls, state, monkeypatch, tmp_path):
    from gingugu import xray_cli

    monkeypatch.setenv("MEMORY_DB_PATH", str(state.parent / "memories.db"))
    _session(calls, state, "FAILED tests/test_svc.py::test_serve - x\n")
    out = io.StringIO()
    with redirect_stdout(out):
        assert xray_cli.main(["score"]) == 0
    assert out.getvalue().startswith("X-RAY SCORE:")
    out = io.StringIO()
    with redirect_stdout(out):
        assert xray_cli.main(["score", "--json"]) == 0
    assert json.loads(out.getvalue())["failed"] == 1
