"""X14: map on arrival.

The X-ray reaches the agent while it reads, not only when it edits. On a
session's first touch of a repo (``Read``, ``Grep`` or ``Glob``) the map of the
repo arrives: its size, languages and the most load-bearing files. Each file the
agent ``Read``s then gets its neighbourhood: who imports it, what it imports,
and where it ranks. Both ride PostToolUse ``additionalContext``, next to the
tool result, and never block. Each says its piece once per session.
"""

from __future__ import annotations

import pytest

from gingugu.xray import read_hook
from tests.test_xray_edit_hook import BASIC, _payload, _repo


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


def _read(repo, rel, session="cc-1", cwd=None):
    out = _payload(repo, rel, tool="Read", session=session, cwd=cwd)
    out["hook_event_name"] = "PostToolUse"
    return out


def _search(repo, tool="Grep", session="cc-1", path=None):
    tool_input = {"pattern": "X"}
    if path is not None:
        tool_input["path"] = str(path)
    return {
        "session_id": session,
        "cwd": str(repo),
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
    }


# --- the map: once per repo per session ----------------------------------------


def test_the_first_read_in_a_repo_brings_the_map(repo, state):
    text = read_hook.check(_read(repo, "pkg/leaf.py"), state)
    assert text is not None
    assert "X-RAY MAP" in text
    assert "pkg/core.py" in text  # the most imported file is on the map


def test_the_map_names_the_repo_size_and_languages(repo, state):
    text = read_hook.check(_read(repo, "pkg/leaf.py"), state)
    assert "6 files" in text
    assert "python" in text


def test_the_map_comes_once_per_repo_per_session(repo, state):
    read_hook.check(_read(repo, "pkg/leaf.py"), state)
    text = read_hook.check(_read(repo, "pkg/a.py"), state) or ""
    assert "X-RAY MAP" not in text


def test_a_new_session_gets_the_map_again(repo, state):
    read_hook.check(_read(repo, "pkg/leaf.py"), state)
    text = read_hook.check(_read(repo, "pkg/leaf.py", session="cc-2"), state)
    assert text is not None and "X-RAY MAP" in text


def test_a_grep_is_a_first_touch(repo, state):
    text = read_hook.check(_search(repo, "Grep"), state)
    assert text is not None and "X-RAY MAP" in text


def test_a_glob_is_a_first_touch(repo, state):
    text = read_hook.check(_search(repo, "Glob"), state)
    assert text is not None and "X-RAY MAP" in text


def test_a_search_rooted_in_a_subdirectory_maps_the_whole_repo(repo, state):
    text = read_hook.check(_search(repo, "Grep", path=repo / "pkg"), state)
    assert text is not None and "6 files" in text


def test_a_search_after_the_map_says_nothing(repo, state):
    read_hook.check(_read(repo, "pkg/leaf.py"), state)
    assert read_hook.check(_search(repo, "Grep"), state) is None


def test_the_map_lists_at_most_eight_files(tmp_path, state):
    files = {"pkg/__init__.py": ""}
    for i in range(12):
        files[f"pkg/m{i}.py"] = "X = 1\n"
        files[f"pkg/u{i}.py"] = f"from pkg import m{i}\n"
    repo = _repo(tmp_path, files)
    text = read_hook.check(_search(repo, "Grep"), state)
    listed = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert 0 < len(listed) <= 8


# --- the neighbourhood: once per file per session -------------------------------


def test_a_read_names_who_imports_the_file(repo, state):
    read_hook.check(_search(repo, "Grep"), state)  # take the map out of the way
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert text is not None
    assert "pkg/core.py" in text
    assert "pkg/a.py" in text and "pkg/b.py" in text


def test_a_read_names_what_the_file_imports(repo, state):
    read_hook.check(_search(repo, "Grep"), state)
    text = read_hook.check(_read(repo, "pkg/a.py"), state)
    assert text is not None and "pkg/core.py" in text


def test_a_read_counts_candidate_tests(repo, state):
    read_hook.check(_search(repo, "Grep"), state)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert "test" in text  # tests/test_a.py reaches core.py through a.py


def test_the_first_read_carries_both_map_and_neighbourhood(repo, state):
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert "X-RAY MAP" in text
    assert "pkg/a.py" in text


def test_the_neighbourhood_comes_once_per_file_per_session(repo, state):
    read_hook.check(_read(repo, "pkg/core.py"), state)
    assert read_hook.check(_read(repo, "pkg/core.py"), state) is None


def test_a_file_with_no_imports_either_way_is_silent(repo, state):
    read_hook.check(_search(repo, "Grep"), state)
    assert read_hook.check(_read(repo, "pkg/leaf.py"), state) is None


def test_a_package_init_is_silent(repo, state):
    read_hook.check(_search(repo, "Grep"), state)
    assert read_hook.check(_read(repo, "pkg/__init__.py"), state) is None


def test_a_neighbourhood_is_short(tmp_path, state):
    files = {"pkg/__init__.py": "", "pkg/core.py": "X = 1\n"}
    for i in range(20):
        files[f"pkg/u{i}.py"] = "from pkg import core\n"
    repo = _repo(tmp_path, files)
    read_hook.check(_search(repo, "Grep"), state)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert len(text.splitlines()) <= 4
    assert "more" in text  # 20 importers, a few named


def test_a_read_does_not_silence_the_edit_xray(repo, state):
    from gingugu.xray import edit_hook

    read_hook.check(_read(repo, "pkg/core.py"), state)
    assert edit_hook.check(_payload(repo, "pkg/core.py"), state) is not None


def test_an_edit_does_not_silence_the_read_neighbourhood(repo, state):
    from gingugu.xray import edit_hook

    edit_hook.check(_payload(repo, "pkg/core.py"), state)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert text is not None and "pkg/a.py" in text


# --- staying out of the way -----------------------------------------------------


def test_other_tools_are_ignored(repo, state):
    for tool in ("Edit", "Write", "Bash", "WebFetch", "mcp__gingugu__memory_recall"):
        payload = _read(repo, "pkg/core.py")
        payload["tool_name"] = tool
        assert read_hook.check(payload, state) is None


def test_no_session_id_is_silent(repo, state):
    payload = _read(repo, "pkg/core.py")
    del payload["session_id"]
    assert read_hook.check(payload, state) is None


def test_outside_a_git_repo_is_silent(tmp_path, state):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "a.py").write_text("X = 1\n")
    payload = {
        "session_id": "cc-1",
        "cwd": str(loose),
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(loose / "a.py")},
    }
    assert read_hook.check(payload, state) is None


def test_reading_a_non_source_file_still_brings_the_map(repo, state):
    (repo / "README.md").write_text("# hi\n")
    text = read_hook.check(_read(repo, "README.md"), state)
    assert text is not None and "X-RAY MAP" in text


def test_reading_a_non_source_file_after_the_map_is_silent(repo, state):
    read_hook.check(_search(repo, "Grep"), state)
    (repo / "README.md").write_text("# hi\n")
    assert read_hook.check(_read(repo, "README.md"), state) is None


def test_bad_tool_input_is_silent(repo, state):
    payload = _read(repo, "pkg/core.py")
    payload["tool_input"] = "not a dict"
    assert read_hook.check(payload, state) is None


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_MAP", "off")
    assert read_hook.enabled() is False
    monkeypatch.setenv("MEMORY_XRAY_MAP", "on")
    assert read_hook.enabled() is True
    monkeypatch.delenv("MEMORY_XRAY_MAP")
    assert read_hook.enabled() is True


def test_the_output_stays_under_the_harness_cap(tmp_path, state):
    files = {"pkg/__init__.py": "", "pkg/core.py": "X = 1\n"}
    for i in range(200):
        files[f"pkg/{'deep_' * 10}u{i}.py"] = "from pkg import core\n"
    repo = _repo(tmp_path, files)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert text is not None and len(text) < 9000


def test_a_control_character_path_cannot_start_a_line(tmp_path, state):
    files = dict(BASIC)
    files["pkg/evil\nINJECTED.py"] = "from pkg import core\n"
    repo = _repo(tmp_path, files)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert text is not None
    assert not any(ln.startswith("INJECTED") for ln in text.splitlines())


def test_the_graph_cache_is_shared_with_the_edit_hook(repo, state):
    from gingugu.xray import edit_hook

    read_hook.check(_read(repo, "pkg/core.py"), state)
    caches = list(state.glob("xray-graph-*.json"))
    edit_hook.check(_payload(repo, "pkg/a.py"), state)
    assert list(state.glob("xray-graph-*.json")) == caches
