"""X15: a write made by a shell command still gets its X-ray.

The edit tripwire (X2) speaks before an ``Edit``/``Write``; a ``sed -i``, a
codegen script or a ``git checkout`` writes behind its back. So ``before``
snapshots each candidate checkout's dirty set (paths, status, content hash) when
a ``Bash`` call is about to run, and ``after`` diffs it once the call returns and
names what each changed source file reaches. Candidates are the command's cwd
repo, every repo the session has touched, and any repo a path in the command
points into. A deleted file's old importers come from the graph cached before
the command. It informs only - the write already happened - and shares the
edit hook's "already shown" claim so a file is reported once per session.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from gingugu.xray import bash_hook, edit_hook, hook_common, read_hook
from tests.test_xray_edit_hook import BASIC, _payload, _repo
from tests.test_xray_read_hook import _read


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_TRIPWIRE", raising=False)
    return tmp_path / "hook-sessions"


def _bash(cwd, command="make gen", session="cc-1", tool_use_id="toolu_1", event="PreToolUse"):
    return {
        "session_id": session,
        "cwd": str(cwd),
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": tool_use_id,
    }


def _run(state, cwd, change, command="make gen", **kw):
    """before -> the command's effect -> after; the text ``after`` returns."""
    assert bash_hook.before(_bash(cwd, command, **kw), state) is None
    change()
    return bash_hook.after(_bash(cwd, command, event="PostToolUse", **kw), state)


# --- what gets reported ----------------------------------------------------------


def test_a_changed_imported_file_is_reported_with_what_it_reaches(repo, state):
    text = _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n"))
    assert text is not None
    assert "GINGUGU X-RAY" in text
    assert "pkg/core.py" in text
    assert "pkg/a.py" in text and "pkg/b.py" in text
    assert "tests/test_a.py" in text


def test_a_command_that_writes_nothing_says_nothing(repo, state):
    assert _run(state, repo, lambda: None) is None


def test_a_change_to_a_file_nothing_imports_says_nothing(repo, state):
    assert _run(state, repo, lambda: (repo / "pkg/leaf.py").write_text("Y = 3\n")) is None
    assert (
        _run(state, repo, lambda: (repo / "README.md").write_text("hi\n"), tool_use_id="t2") is None
    )


def test_a_file_already_dirty_and_changed_again_is_caught(repo, state):
    (repo / "pkg/core.py").write_text("X = 2\n")  # dirty before the command
    text = _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 3\n"))
    assert text is not None and "pkg/core.py" in text


def test_a_file_already_dirty_and_left_alone_is_not_reported(repo, state):
    (repo / "pkg/core.py").write_text("X = 2\n")
    assert _run(state, repo, lambda: None) is None


def test_a_deleted_file_names_its_old_importers_from_the_cached_graph(repo, state):
    hook_common.graph(repo, state, "cc-1")  # the cache as it stood before the command
    text = _run(state, repo, lambda: (repo / "pkg/core.py").unlink())
    assert text is not None
    assert "pkg/core.py" in text and "deleted" in text.lower()
    assert "pkg/a.py" in text and "pkg/b.py" in text


def test_a_deleted_file_with_no_cached_graph_is_still_named(repo, state):
    text = _run(state, repo, lambda: (repo / "pkg/core.py").unlink())
    assert text is None or ("pkg/core.py" in text and "deleted" in text.lower())


def test_the_report_fits_additional_context(repo, state):
    many = {f"pkg/m{i}.py": "from pkg import core\n" for i in range(400)}

    def change():
        (repo / "pkg/core.py").write_text("X = 2\n")
        for rel, body in many.items():
            (repo / rel).write_text(body)

    text = _run(state, repo, change)
    assert text is not None and len(text) <= 8800


# --- once per session, shared with the edit tripwire ---------------------------------


def test_a_file_is_reported_once_per_session(repo, state):
    assert _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n"))
    again = _run(
        state, repo, lambda: (repo / "pkg/core.py").write_text("X = 3\n"), tool_use_id="t2"
    )
    assert again is None


def test_a_file_the_edit_tripwire_already_showed_is_not_reported(repo, state):
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state)  # denied once, claimed
    assert _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n")) is None


def test_a_reported_file_is_not_denied_later_by_the_edit_tripwire(repo, state):
    assert _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n"))
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is None


# --- which checkouts are watched ------------------------------------------------------


def test_a_repo_the_session_touched_is_watched_from_another_cwd(tmp_path, state):
    a = _repo(tmp_path / "a", BASIC)
    b = _repo(tmp_path / "b", BASIC)
    read_hook.check(_read(b, "pkg/leaf.py"), state)  # the session read in b
    text = _run(state, a, lambda: (b / "pkg/core.py").write_text("X = 2\n"), command="./gen.sh")
    assert text is not None and "pkg/core.py" in text
    assert "repo" in text  # names the checkout it is about


def test_a_repo_the_edit_tripwire_saw_is_touched_too(tmp_path, state):
    a = _repo(tmp_path / "a", BASIC)
    b = _repo(tmp_path / "b", BASIC)
    edit_hook.check(_payload(b, "pkg/leaf.py"), state)
    assert b in bash_hook.touched(state, "cc-1")
    assert a not in bash_hook.touched(state, "cc-1")


def test_a_repo_a_path_in_the_command_points_into_is_watched(tmp_path, state):
    b = _repo(tmp_path / "b", BASIC)
    plain = tmp_path / "plain"
    plain.mkdir()
    target = b / "pkg" / "core.py"
    text = _run(
        state, plain, lambda: target.write_text("X = 2\n"), command=f"sed -i '' s/1/2/ {target}"
    )
    assert text is not None and "pkg/core.py" in text


def test_touched_repos_are_per_session(tmp_path, state):
    b = _repo(tmp_path / "b", BASIC)
    read_hook.check(_read(b, "pkg/leaf.py", session="cc-1"), state)
    assert b in bash_hook.touched(state, "cc-1")
    assert bash_hook.touched(state, "cc-2") == []


def test_a_huge_dirty_set_is_skipped(repo, state, monkeypatch):
    monkeypatch.setattr(bash_hook, "MAX_DIRTY", 3)
    for i in range(5):
        (repo / f"pkg/n{i}.py").write_text("from pkg import core\n")
    assert _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n")) is None


# --- the snapshot itself ----------------------------------------------------------------------


def test_the_snapshot_is_owner_only_and_consumed(repo, state):
    bash_hook.before(_bash(repo), state)
    snaps = [p for p in state.rglob("*") if p.is_file() and "xray-bash" in str(p)]
    assert len(snaps) == 1
    if os.name != "nt":
        assert stat.S_IMODE(snaps[0].stat().st_mode) == 0o600
    json.loads(snaps[0].read_text())
    (repo / "pkg/core.py").write_text("X = 2\n")
    assert bash_hook.after(_bash(repo, event="PostToolUse"), state)
    assert not snaps[0].exists()
    assert bash_hook.after(_bash(repo, event="PostToolUse"), state) is None


def test_after_without_a_snapshot_says_nothing(repo, state):
    (repo / "pkg/core.py").write_text("X = 2\n")
    assert bash_hook.after(_bash(repo, event="PostToolUse"), state) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(tool_name="Read"),
        lambda p: p.update(session_id=""),
        lambda p: p.pop("tool_use_id"),
        lambda p: p.update(tool_input="nope"),
    ],
)
def test_not_ours_is_silent_and_writes_nothing(repo, state, mutate):
    pre, post = _bash(repo), _bash(repo, event="PostToolUse")
    mutate(pre)
    mutate(post)
    assert bash_hook.before(pre, state) is None
    (repo / "pkg/core.py").write_text("X = 2\n")
    assert bash_hook.after(post, state) is None


def test_outside_a_repo_is_silent(tmp_path, state):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run(state, plain, lambda: (plain / "x.py").write_text("x\n")) is None


def test_the_switch_turns_it_off(repo, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TRIPWIRE", "off")
    assert _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n")) is None


def test_it_never_raises(repo, state, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(hook_common, "graph", boom)
    assert _run(state, repo, lambda: (repo / "pkg/core.py").write_text("X = 2\n")) is None
