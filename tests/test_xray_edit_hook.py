"""X2: the X-ray tripwire on edit.

The first ``Edit``/``Write``/``MultiEdit`` of a ``.py`` file in a session is
denied with the file's dependents as the reason; the re-issued call passes.
Deny is the only lever: PreToolUse ``additionalContext`` lands next to the tool
RESULT, after the edit. The graph is built on this machine from the checkout and
cached per repo by fingerprint, so it never needs the brain.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gingugu.xray import edit_hook, extract


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# core.py is imported by a.py and b.py; a.py by tests/test_a.py; leaf.py by nothing.
BASIC = {
    "pkg/__init__.py": "",
    "pkg/core.py": "X = 1\n",
    "pkg/a.py": "from pkg import core\n",
    "pkg/b.py": "import pkg.core\n",
    "pkg/leaf.py": "Y = 2\n",
    "tests/test_a.py": "from pkg import a\n",
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


def _payload(repo, rel, tool="Edit", session="cc-1", key="file_path", cwd=None):
    return {
        "session_id": session,
        "cwd": str(cwd or repo),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {key: str(repo / rel)},
    }


# --- edit_hook.check ----------------------------------------------------------


def test_an_edit_to_an_imported_file_names_its_dependents(repo, state):
    reason = edit_hook.check(_payload(repo, "pkg/core.py"), state)
    assert reason is not None
    assert "pkg/core.py" in reason
    assert "pkg/a.py" in reason and "pkg/b.py" in reason
    assert "tests/test_a.py" in reason  # transitive, as a candidate test
    assert "re-issue" in reason


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_every_edit_tool_is_checked(repo, state, tool):
    assert edit_hook.check(_payload(repo, "pkg/core.py", tool=tool), state) is not None


def test_the_same_file_fires_once_per_session(repo, state):
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is not None
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is None
    assert edit_hook.check(_payload(repo, "pkg/core.py", tool="Write"), state) is None


def test_another_file_and_another_session_still_fire(repo, state):
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is not None
    assert edit_hook.check(_payload(repo, "pkg/a.py"), state) is not None
    assert edit_hook.check(_payload(repo, "pkg/core.py", session="cc-2"), state) is not None


def test_a_leaf_is_silent(repo, state):
    assert edit_hook.check(_payload(repo, "pkg/leaf.py"), state) is None


def test_a_new_file_is_silent(repo, state):
    assert edit_hook.check(_payload(repo, "pkg/new.py", tool="Write"), state) is None


def test_non_python_and_non_edit_tools_are_silent(repo, state):
    (repo / "README.md").write_text("hi\n")
    assert edit_hook.check(_payload(repo, "README.md"), state) is None
    assert edit_hook.check(_payload(repo, "pkg/core.py", tool="Read"), state) is None
    nb = _payload(repo, "pkg/core.py", tool="NotebookEdit", key="notebook_path")
    assert edit_hook.check(nb, state) is None


def test_a_file_outside_any_git_repo_is_silent(tmp_path, state):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "m.py").write_text("import os\n")
    assert edit_hook.check(_payload(loose, "m.py"), state) is None


def test_a_relative_path_resolves_against_cwd(repo, state):
    payload = _payload(repo, "pkg/core.py", cwd=repo / "pkg")
    payload["tool_input"] = {"file_path": "core.py"}
    assert edit_hook.check(payload, state) is not None


def test_malformed_input_is_silent(repo, state):
    for tool_input in (None, "x", {}, {"file_path": 7}, {"file_path": ""}):
        payload = _payload(repo, "pkg/core.py")
        payload["tool_input"] = tool_input
        assert edit_hook.check(payload, state) is None


def test_dependents_are_capped_with_a_count_of_the_rest(tmp_path, state):
    files = {"pkg/__init__.py": "", "pkg/core.py": "X = 1\n"}
    for i in range(8):
        files[f"pkg/m{i}.py"] = "from pkg import core\n"
    repo = _repo(tmp_path, files)
    reason = edit_hook.check(_payload(repo, "pkg/core.py"), state)
    listed = [i for i in range(8) if f"pkg/m{i}.py" in reason]
    assert len(listed) == edit_hook.TOP_N
    assert f"{8 - edit_hook.TOP_N} more" in reason


# --- the graph cache ----------------------------------------------------------


def _count_extracts(monkeypatch) -> list[int]:
    calls = [0]
    real = extract.extract

    def counting(path):
        calls[0] += 1
        return real(path)

    monkeypatch.setattr(extract, "extract", counting)
    return calls


def test_an_unchanged_checkout_reuses_the_cached_graph(repo, state, monkeypatch):
    calls = _count_extracts(monkeypatch)
    edit_hook.check(_payload(repo, "pkg/core.py"), state)
    edit_hook.check(_payload(repo, "pkg/a.py"), state)
    assert calls[0] == 1


def test_a_changed_checkout_rebuilds_the_graph(repo, state, monkeypatch):
    calls = _count_extracts(monkeypatch)
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is not None
    (repo / "pkg/leaf.py").write_text("from pkg import a\n")  # leaf now imports a
    assert calls[0] == 1
    reason = edit_hook.check(_payload(repo, "pkg/a.py"), state)
    assert calls[0] == 2
    assert "pkg/leaf.py" in reason


def test_a_corrupt_cache_is_rebuilt_not_fatal(repo, state):
    edit_hook.check(_payload(repo, "pkg/core.py"), state)
    for f in state.glob("xray-graph-*.json"):
        f.write_text("{not json")
    assert edit_hook.check(_payload(repo, "pkg/a.py"), state) is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_cache_and_state_files_are_owner_only(repo, state):
    edit_hook.check(_payload(repo, "pkg/core.py"), state)
    written = list(state.glob("xray-*.json"))
    assert written
    for f in written:
        assert f.stat().st_mode & 0o777 == 0o600


def test_an_extraction_failure_is_silent(repo, state, monkeypatch):
    def boom(path):
        raise extract.XrayError("nope")

    monkeypatch.setattr(extract, "extract", boom)
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is None


def test_candidate_tests_list_runnable_tests_nearest_first(tmp_path, state):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "X = 1\n",
            "pkg/a.py": "from pkg import core\n",
            "tests/helpers.py": "from pkg import core\n",
            "tests/test_far.py": "from pkg import a\n",
            "tests/test_near.py": "from pkg import core\n",
        },
    )
    reason = edit_hook.check(_payload(repo, "pkg/core.py"), state)
    line = next(ln for ln in reason.splitlines() if ln.startswith("Candidate tests:"))
    assert line.index("test_near.py") < line.index("test_far.py") < line.index("helpers.py")
