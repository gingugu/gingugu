"""X-ray tuned for the agent that uses it: deny only a blind edit, tell the rest after.

A deny the agent has learned to re-issue unread is friction, not information.
The import blast of a file is already in front of the agent once its
neighbourhood was shown on ``Read`` (or once the agent wrote the file itself),
so the edit tripwire denies only a *blind* edit: a file with importers whose
X-ray this session never showed. Everything else edits freely, and a PostToolUse
note right after the edit names the cross-file callers of the definitions it
touched and the tests nearest them - when there is something to fix, with the
names needed to fix it.

Every surface leads with what is directly connected: direct importers and
direct callers, and the nearest tests. A transitive total stays as one number;
in a repo with a hub (``server.py`` imports everything) it says little else.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_hook, edit_note, read_hook
from tests.test_xray_edit_hook import BASIC, _payload, _repo


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


def _read(repo, rel, session="cc-1"):
    out = _payload(repo, rel, tool="Read", session=session)
    out["hook_event_name"] = "PostToolUse"
    return out


def _after(repo, rel, tool="Edit", session="cc-1", **tool_input):
    out = _payload(repo, rel, tool=tool, session=session)
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"].update(tool_input)
    return out


# --- the deny: blind edits only ----------------------------------------------------


def test_a_blind_edit_of_an_imported_file_is_still_denied(repo, state):
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is not None


def test_an_edit_after_the_file_was_read_is_not_denied(repo, state):
    read_hook.check(_read(repo, "pkg/core.py"), state)
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is None


def test_a_read_in_another_session_does_not_count(repo, state):
    read_hook.check(_read(repo, "pkg/core.py", session="cc-2"), state)
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is not None


def test_a_file_this_session_already_edited_is_not_denied_again_by_a_write(repo, state):
    edit_note.check(_after(repo, "pkg/core.py", tool="Write", content="X = 2\n"), state)
    assert edit_hook.check(_payload(repo, "pkg/core.py", tool="Edit"), state) is None


def test_the_deny_leads_with_direct_importers(tmp_path, state):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "X = 1\n",
            "pkg/a.py": "from pkg import core\n",
            "pkg/b.py": "from pkg import a\n",
            "tests/test_b.py": "from pkg import b\n",
        },
    )
    reason = edit_hook.check(_payload(repo, "pkg/core.py"), state)
    lines = reason.splitlines()
    assert lines[0] == (
        "GINGUGU X-RAY: pkg/core.py is imported directly by 1 file; "
        "a change reaches 3 files (1 candidate test)."
    )
    assert "Direct importers by rank: pkg/a.py" in lines
    assert "pkg/b.py" not in "\n".join(ln for ln in lines if ln.startswith("Direct"))
    assert any(ln.startswith("Nearest tests: tests/test_b.py") for ln in lines)
    assert "Top dependents" not in reason


# --- the read neighbourhood: direct first, nearest tests, no transitive line --------


def test_the_neighbourhood_names_nearest_tests_instead_of_a_transitive_total(repo, state):
    read_hook.check(
        {**_read(repo, "pkg/core.py"), "tool_name": "Grep", "tool_input": {"pattern": "X"}},
        state,
    )
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert "transitively" not in text
    assert "Nearest tests: tests/test_a.py" in text


def test_the_map_leaves_dot_directories_out_of_entry_points(tmp_path, state):
    repo = _repo(
        tmp_path,
        {
            ".claude/hooks/h.py": "if __name__ == '__main__':\n    pass\n",
            "cli/__main__.py": "import lib\n",
            "lib.py": "X = 1\n",
        },
    )
    text = read_hook.check(_read(repo, "lib.py"), state)
    entries = next(ln for ln in text.splitlines() if ln.startswith("Entry points"))
    assert "cli/__main__.py" in entries
    assert ".claude" not in entries


# --- the note after an edit ----------------------------------------------------------

CALLS = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "def f():\n"  # 1
        "    return 1\n"  # 2
        "\n"  # 3
        "def g():\n"  # 4
        "    return f()\n"  # 5
        "\n"  # 6
        "def lonely():\n"  # 7
        "    return 0\n"  # 8
    ),
    "pkg/svc.py": "from pkg.core import f\n\ndef serve():\n    return f()\n",
    "pkg/cli.py": "from pkg import svc\n\ndef main():\n    svc.serve()\n",
    "tests/test_svc.py": "from pkg import svc\n\ndef test_serve():\n    svc.serve()\n",
    "tests/test_core.py": "from pkg.core import f\n\ndef test_f():\n    f()\n",
}


@pytest.fixture
def calls(tmp_path):
    return _repo(tmp_path, CALLS)


def _edit(repo, rel, old, new, state):
    """An Edit as it lands: the file is rewritten first, then PostToolUse fires."""
    path = repo / rel
    path.write_text(path.read_text().replace(old, new, 1))
    return edit_note.check(_after(repo, rel, old_string=old, new_string=new), state)


def test_an_edit_names_cross_file_callers_and_their_nearest_tests(calls, state):
    edit_note.check(_read(calls, "pkg/core.py"), state)  # not an edit tool: ignored
    text = _edit(calls, "pkg/core.py", "    return 1", "    return 2", state)
    lines = text.splitlines()
    assert lines[0] == "GINGUGU X-RAY after edit (calls, a lower bound):"
    assert lines[1] == (
        "`f` (L1): 2 callers in 2 other files - pkg/svc.py:serve, tests/test_core.py:test_f. "
        "Tests: tests/test_core.py, tests/test_svc.py."
    )


def test_same_file_callers_alone_say_nothing(calls, state):
    assert _edit(calls, "pkg/core.py", "    return f()", "    return f() + 1", state) is None


def test_a_definition_nobody_calls_says_nothing(calls, state):
    assert _edit(calls, "pkg/core.py", "    return 0", "    return -1", state) is None


def test_each_definition_is_named_once_per_session(calls, state):
    assert _edit(calls, "pkg/core.py", "    return 1", "    return 2", state) is not None
    assert _edit(calls, "pkg/core.py", "    return 2", "    return 3", state) is None
    other = _after(calls, "pkg/core.py", session="cc-2", old_string="x", new_string="    return 3")
    assert edit_note.check(other, state) is not None


def test_a_multi_edit_names_each_definition(calls, state):
    edit_note.check(_read(calls, "pkg/core.py"), state)
    p = _after(
        calls,
        "pkg/core.py",
        tool="MultiEdit",
        edits=[
            {"old_string": "x", "new_string": "    return 1"},
            {"old_string": "y", "new_string": "    return 0"},
        ],
    )
    text = edit_note.check(p, state)
    assert "`f` (L1)" in text and "lonely" not in text


def test_the_note_needs_no_rebuild_after_the_edit(calls, state, monkeypatch):
    from gingugu.xray import extract

    read_hook.check(_read(calls, "pkg/core.py"), state)  # the hooks built the caches
    monkeypatch.setattr(extract, "extract", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert _edit(calls, "pkg/core.py", "    return 1", "    return 2", state) is not None


def test_a_write_and_non_python_files_add_no_note(calls, state):
    assert (
        edit_note.check(_after(calls, "pkg/core.py", tool="Write", content="X = 1\n"), state)
        is None
    )
    (calls / "README.md").write_text("hi\n")
    assert (
        edit_note.check(_after(calls, "README.md", old_string="a", new_string="hi"), state) is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tool_name": "Edit"},
        {"tool_name": "Edit", "session_id": "s", "tool_input": "x"},
        {"tool_name": "Edit", "session_id": "", "tool_input": {"file_path": "/nope.py"}},
    ],
)
def test_malformed_input_is_silent(payload, state):
    assert edit_note.check(payload, state) is None


def test_the_note_is_switched_off_with_the_tripwire(calls, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TRIPWIRE", "off")
    assert _edit(calls, "pkg/core.py", "    return 1", "    return 2", state) is None
