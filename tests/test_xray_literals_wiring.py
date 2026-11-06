"""X-ray literal consumers in the post-edit note (board d5639dd5).

``edit_note.check`` (PostToolUse) is where an edit that removed a quoted literal
learns who still names it: labelled as text matches, bounded, once per literal
per session, and silent on any failure.
"""

from __future__ import annotations

import time

import pytest

from gingugu.xray import edit_note, literals, scan
from gingugu.xray.scan import XrayError
from tests.test_xray_edit_hook import _payload, _repo
from tests.test_xray_literals import CONTRACT


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def contract(tmp_path):
    return _repo(tmp_path, CONTRACT)


def _edit(repo, rel, old, new, session="cc-1", tool="Edit"):
    """The PostToolUse payload of an edit already applied to disk."""
    path = repo / rel
    path.write_text(path.read_text().replace(old, new))
    out = _payload(repo, rel, tool=tool, session=session)
    out["hook_event_name"] = "PostToolUse"
    if tool == "MultiEdit":
        out["tool_input"]["edits"] = [{"old_string": old, "new_string": new}]
    else:
        out["tool_input"].update(old_string=old, new_string=new)
    return out


RENAME = ('{"status": "ok"}', '{"outcome": "ok"}')


def _literal_lines(note):
    return [ln for ln in (note or "").splitlines() if literals.LABEL in ln]


def test_a_renamed_key_names_its_text_readers(contract, state):
    note = edit_note.check(_edit(contract, "sync/cli.py", *RENAME), state)
    (line,) = _literal_lines(note)
    assert "`status`" in line and "removed by this edit" in line
    assert "tests/test_cli.py:2" in line
    assert "docs/runbook.md:1" in line


def test_the_line_is_labelled_as_text_and_never_as_an_import_edge(contract, state):
    note = edit_note.check(_edit(contract, "sync/cli.py", *RENAME), state)
    (line,) = _literal_lines(note)
    assert line.lstrip("- ").startswith(literals.LABEL)
    assert "import" not in line.lower().replace(literals.LABEL.lower(), "")


def test_the_note_has_a_header_when_the_literal_line_is_all_it_says(contract, state):
    note = edit_note.check(_edit(contract, "sync/cli.py", *RENAME), state)
    assert note.splitlines()[0].startswith("GINGUGU X-RAY after edit")


def test_a_multiedit_is_swept_too(contract, state):
    note = edit_note.check(_edit(contract, "sync/cli.py", *RENAME, tool="MultiEdit"), state)
    assert _literal_lines(note)


def test_a_write_names_no_removed_literal(contract, state):
    payload = _payload(contract, "sync/cli.py", tool="Write")
    payload["hook_event_name"] = "PostToolUse"
    payload["tool_input"]["content"] = 'print("outcome")\n'
    assert not _literal_lines(edit_note.check(payload, state))


def test_a_non_xray_language_file_is_swept(contract, state):
    note = edit_note.check(_edit(contract, "fixtures/line.json", '"status"', '"outcome"'), state)
    (line,) = _literal_lines(note)
    assert "tests/test_cli.py:2" in line and "fixtures/line.json" not in line


def test_an_edit_whose_literal_nobody_reads_says_nothing(tmp_path, state):
    repo = _repo(tmp_path, {"sync/cli.py": 'print({"lonely_key": 1})\n'})
    note = edit_note.check(_edit(repo, "sync/cli.py", '"lonely_key"', '"other_key"'), state)
    assert not _literal_lines(note)


def test_named_locations_are_capped_with_a_count_of_the_rest(tmp_path, state):
    files = {"sync/cli.py": 'print({"run_ref": 1})\n'}
    files |= {f"tests/test_{i}.py": 'assert x["run_ref"]\n' for i in range(literals.NAMED + 3)}
    repo = _repo(tmp_path, files)
    (line,) = _literal_lines(
        edit_note.check(_edit(repo, "sync/cli.py", '"run_ref"', '"run_id"'), state)
    )
    assert line.count("tests/test_") == literals.NAMED
    assert "(+3 more)" in line


def test_a_too_common_literal_gets_a_count_not_a_list(tmp_path, state):
    n = literals.TOO_COMMON + 5
    files = {"sync/cli.py": 'print({"state": 1})\n'}
    files |= {f"app/m{i}.py": 'x = row["state"]\n' for i in range(n)}
    repo = _repo(tmp_path, files)
    (line,) = _literal_lines(
        edit_note.check(_edit(repo, "sync/cli.py", '"state"', '"phase"'), state)
    )
    assert f"{n} files" in line and "too common to list" in line
    assert "app/m" not in line


def test_each_literal_is_named_once_per_session(contract, state):
    edit_note.check(_edit(contract, "sync/cli.py", *RENAME), state)
    again = _edit(contract, "sync/cli.py", '{"outcome": "ok"}', '{"status": "ok"}')
    back = _edit(contract, "sync/cli.py", *RENAME)
    edit_note.check(again, state)
    assert not _literal_lines(edit_note.check(back, state))
    other = _edit(contract, "sync/cli.py", '{"outcome": "ok"}', '{"status": "ok"}', session="cc-2")
    edit_note.check(other, state)
    assert _literal_lines(edit_note.check(_edit(contract, "sync/cli.py", *RENAME, "cc-2"), state))


def test_the_tripwire_switch_turns_it_off(contract, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TRIPWIRE", "off")
    assert edit_note.check(_edit(contract, "sync/cli.py", *RENAME), state) is None


def test_a_git_failure_drops_the_line_and_never_raises(contract, state, monkeypatch):
    def boom(*a, **k):
        raise XrayError("git grep took over 1.0s")

    monkeypatch.setattr(literals, "consumers", boom)
    note = edit_note.check(_edit(contract, "sync/cli.py", *RENAME), state)
    assert not _literal_lines(note)
    payload = _edit(contract, "sync/cli.py", '"outcome"', '"status"')
    assert literals.lines(payload, contract, "sync/cli.py", state, "cc-1") == []


def test_every_grep_runs_under_the_timeout(contract, state, monkeypatch):
    seen = []
    real = scan.git

    def spy(cwd, *args, timeout=None):
        if args and args[0] == "grep":
            seen.append(timeout)
        return real(cwd, *args, timeout=timeout)

    monkeypatch.setattr(scan, "git", spy)
    literals.consumers(contract, "status", exclude="sync/cli.py")
    assert seen and all(t == literals.GIT_TIMEOUT_S for t in seen)


def test_the_sweep_stays_inside_the_hook_budget(tmp_path, state):
    # 1500 files, three removed literals each named in a third of them: the
    # measured cost on a 545-file checkout is ~25 ms a literal; 1.5 s is the cap.
    keys = ("alpha_key", "beta_key", "gamma_key")
    files = {"sync/cli.py": "print({" + ", ".join(f'"{k}": 1' for k in keys) + "})\n"}
    for i in range(1500):
        files[f"app/m{i}.py"] = f'x = row["{keys[i % 3]}"]\n' + "y = 1\n" * 40
    repo = _repo(tmp_path, files)
    old = ", ".join(f'"{k}": 1' for k in keys)
    payload = _edit(repo, "sync/cli.py", old, '"other": 1')
    start = time.perf_counter()
    out = literals.lines(payload, repo, "sync/cli.py", state, "cc-1")
    assert time.perf_counter() - start < 1.5
    assert len(out) == 3
