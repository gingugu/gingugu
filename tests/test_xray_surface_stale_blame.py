"""The after-edit surface diff names only reach the edit itself made. The "before"
map is the symbol cache as last written, which can predate a pull, a branch switch
or another session's edits; a reach change made out of band must not be blamed on
whatever file is edited next. An edit adds call edges only from its own file, so a
newly reachable sink counts only when its path crosses a definition in that file.
"""

from __future__ import annotations

from gingugu.xray import edit_note
from tests.test_xray_edit_hook import _repo
from tests.test_xray_surface_hooks import FILES, _edited, _surface, _warm
from tests.test_xray_surface_hooks import state as _state

state = _state  # the fixture, shared

TEST_FILE = {"tests/test_x.py": "def test_a():\n    assert 1\n"}
OUT_OF_BAND = ("    return s.all()\n", "    return jobs.go(s.all())\n")  # GET /items -> exec
SYSTEM = (
    "        return self.db.execute('SELECT * FROM users')\n",
    "        import os\n        os.system(self.db)\n"
    "        return self.db.execute('SELECT * FROM users')\n",
)


def _repo_with_drift(tmp_path, state):
    repo = _repo(tmp_path, dict(FILES, **TEST_FILE))
    _warm(repo, state)
    path = repo / "app/server.py"  # changed with no hook watching: a pull, another session
    path.write_text(path.read_text().replace(*OUT_OF_BAND))
    return repo


def _reach(text) -> list[str]:
    return [ln for ln in _surface(text) if "reachable from" in ln]


def test_a_test_file_edit_is_not_blamed_for_drift(tmp_path, state):
    repo = _repo_with_drift(tmp_path, state)
    text = edit_note.check(_edited(repo, "tests/test_x.py", "assert 1", "assert 2"), state)
    assert _surface(text) == []


def test_an_unrelated_source_edit_is_not_blamed_for_drift(tmp_path, state):
    repo = _repo_with_drift(tmp_path, state)
    text = edit_note.check(
        _edited(repo, "app/jobs.py", "    return x\n", "    return x + 1\n"), state
    )
    assert _reach(text) == []


def test_reach_made_through_a_callee_file_is_still_named(tmp_path, state):
    repo = _repo(tmp_path, FILES)
    _warm(repo, state)
    text = edit_note.check(_edited(repo, "app/store.py", *SYSTEM), state)
    assert any(
        ln.startswith("- Surface: this edit makes exec os.system (app/store.py:")
        and "reachable from http GET /items" in ln
        for ln in _reach(text)
    ), _surface(text)


def test_with_drift_and_a_real_edit_only_the_real_reach_is_named(tmp_path, state):
    repo = _repo_with_drift(tmp_path, state)
    text = edit_note.check(_edited(repo, "app/store.py", *SYSTEM), state)
    reach = _reach(text)
    assert reach and all("os.system" in ln for ln in reach), _surface(text)
