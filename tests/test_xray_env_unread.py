"""X7a, carried from X7c's security review: never claim 'nowhere' past an unread file.

A file too big to read (or cut at the name cap) may set or read any name, so while
one exists the env map's hooks and tool never say a name is set or read nowhere
here: they say it is not in any file read, and name the files that were not.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_note, env_hook, env_map, env_tool
from tests.test_xray_edit_hook import _payload, _repo

FILES = {
    "deploy/docker-compose.yml": (
        "services:\n  web:\n    environment:\n      - DB_URL=x\n" + "# pad\n" * 60
    ),
    "app/settings.py": 'import os\nDB = os.getenv("DB_URL")\n',
    "Dockerfile": "FROM x\nENV PORT_X=1\n",
}
UNREAD = "deploy/docker-compose.yml"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_ENV", raising=False)
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(env_map, "MAX_FILE_BYTES", 200)
    return _repo(tmp_path, FILES)


def _written(repo, rel, text):
    (repo / rel).write_text(text)
    out = _payload(repo, rel, tool="Write")
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"]["content"] = text
    return out


def test_a_file_too_big_to_read_is_named_unread(repo, state):
    m = env_map.read(repo, state)
    assert m.unread == (UNREAD,)
    assert "DB_URL" not in m.sets


def test_a_file_cut_at_the_name_cap_is_named_unread(repo, state, monkeypatch):
    monkeypatch.setattr(env_map, "MAX_FILE_BYTES", 262_144)
    monkeypatch.setattr(env_map, "MAX_NAMES", 0)
    assert env_map.read(repo, state).unread == ("Dockerfile", "app/settings.py", UNREAD)


def test_the_read_hook_never_says_set_nowhere_past_an_unread_file(repo, state):
    line = env_hook.file_line(repo, "app/settings.py", state) or ""
    assert "nowhere" not in line
    assert f"DB_URL (not set in any file read; {UNREAD} is too big)" in line


def test_the_read_hook_never_says_read_nowhere_past_an_unread_file(repo, state):
    line = env_hook.file_line(repo, "Dockerfile", state) or ""
    assert "nowhere" not in line
    assert f"PORT_X (not read in any file read; {UNREAD} is too big)" in line


def test_the_edit_note_never_says_set_nowhere_past_an_unread_file(repo, state):
    out = _written(repo, "app/new.py", 'import os\nos.getenv("BRAND_NEW")\n')
    assert "set nowhere" not in (edit_note.check(out, state) or "")


def test_the_tool_flags_softly_and_names_the_unread_files(repo, state):
    out = env_tool.answer(str(repo))
    rows = {r["name"]: r for r in out["names"]}
    assert rows["DB_URL"]["flag"] == "not set in any file read"
    assert rows["PORT_X"]["flag"] == "not read in any file read"
    assert out["unread"] == [UNREAD]
    assert (out["totals"]["never_set"], out["totals"]["never_read"]) == (0, 0)
    assert UNREAD in out["text"]


def test_the_tool_for_named_files_never_says_nowhere(repo, state):
    out = env_tool.answer(str(repo), "app/settings.py")
    assert "nowhere here" not in out["text"]
    assert "reads DB_URL: set in no file read" in out["text"]
    assert out["unread"] == [UNREAD]


def test_a_complete_read_keeps_the_old_wording(tmp_path, state):
    whole = _repo(tmp_path / "whole", {"app/a.py": 'import os\nos.getenv("ONLY_READ")\n'})
    assert env_map.read(whole, state).unread == ()
    assert "ONLY_READ (set nowhere here)" in env_hook.file_line(whole, "app/a.py", state)
    out = env_tool.answer(str(whole))
    assert out["unread"] == [] and out["names"][0]["flag"] == "never set here"
