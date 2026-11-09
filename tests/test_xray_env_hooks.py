"""X7a in the hooks: a file's first read names the env vars it reads and sets, with
the other side of each; an edit that starts reading a name set nowhere in the repo
says so, once per name per session. ``MEMORY_XRAY_ENV=off`` silences both.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_note, read_hook
from tests.test_xray_edit_hook import _payload, _repo
from tests.test_xray_env_map import FILES


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_ENV", raising=False)
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


def _read(repo, rel, session="cc-1"):
    out = _payload(repo, rel, tool="Read", session=session)
    out["hook_event_name"] = "PostToolUse"
    return out


def _written(repo, rel, text, session="cc-1"):
    (repo / rel).write_text(text)
    out = _payload(repo, rel, tool="Write", session=session)
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"]["content"] = text
    return out


def _edited(repo, rel, old, new, session="cc-1"):
    path = repo / rel
    path.write_text(path.read_text().replace(old, new))
    out = _payload(repo, rel, tool="Edit", session=session)
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"].update(old_string=old, new_string=new)
    return out


# --- the read hook -------------------------------------------------------------------


def test_a_first_read_names_what_the_file_reads_and_where_it_is_set(repo, state):
    text = read_hook.check(_read(repo, "app/settings.py"), state)
    line = next(ln for ln in text.splitlines() if ln.startswith("Env: "))
    assert "reads DB_URL (set in docker-compose.yml:5)" in line
    assert "RUN_MODE (set in docker-compose.yml:9, tests/test_settings.py:2)" in line
    assert read_hook.check(_read(repo, "app/settings.py"), state) is None  # once


def test_a_name_set_nowhere_says_so(repo, state):
    text = read_hook.check(_read(repo, "cmd/main.go"), state)
    assert "GO_HOST (set nowhere here)" in text


def test_a_config_file_names_who_reads_what_it_sets(repo, state):
    text = read_hook.check(_read(repo, "Dockerfile"), state)
    line = next(ln for ln in text.splitlines() if ln.startswith("Env: "))
    assert "sets GO_PORT (read by cmd/main.go:3)" in line
    assert "BUILD_REV (read nowhere here)" in line


def test_a_file_with_no_env_has_no_env_line(repo, state):
    text = read_hook.check(_read(repo, "web/client.ts"), state) or ""
    assert "Env: " in text  # reads three
    other = _repo(repo.parent / "other", {"pkg/a.py": "X = 1\n"})
    assert "Env: " not in (read_hook.check(_read(other, "pkg/a.py"), state) or "")


def test_many_names_are_cut(repo, state):
    many = "import os\n" + "".join(f'os.getenv("MANY_{i}")\n' for i in range(9))
    (repo / "app/many.py").write_text(many)
    text = read_hook.check(_read(repo, "app/many.py"), state)
    line = next(ln for ln in text.splitlines() if ln.startswith("Env: "))
    assert "(+5 more" in line and "code_xray env" in line


# --- the edit note -------------------------------------------------------------------


def test_an_edit_reading_a_name_set_nowhere_says_so_once(repo, state):
    out = _edited(repo, "app/settings.py", "import os\n", 'import os\nK = os.getenv("BRAND_NEW")\n')
    text = edit_note.check(out, state)
    assert "Env: reads BRAND_NEW, set nowhere in this repo" in text
    again = _edited(repo, "app/settings.py", "K = ", "K2 = ")
    assert "BRAND_NEW" not in (edit_note.check(again, state) or "")


def test_an_edit_reading_a_name_that_is_set_says_nothing_of_it(repo, state):
    out = _edited(repo, "app/settings.py", "import os\n", 'import os\nK = os.getenv("GO_PORT")\n')
    assert "GO_PORT" not in (edit_note.check(out, state) or "")


def test_a_write_counts_like_an_edit(repo, state):
    out = _written(repo, "app/new.py", 'import os\nos.environ["ANOTHER_NEW"]\n')
    assert "Env: reads ANOTHER_NEW, set nowhere in this repo" in edit_note.check(out, state)


def test_a_name_already_read_before_the_edit_is_not_news(repo, state):
    out = _edited(repo, "cmd/main.go", "package main\n", "package main // touched\n")
    assert "GO_HOST" not in (edit_note.check(out, state) or "")


# --- the switch -----------------------------------------------------------------------


def test_the_switch_off_silences_both(repo, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_ENV", "off")
    assert "Env: " not in (read_hook.check(_read(repo, "app/settings.py"), state) or "")
    out = _edited(repo, "app/settings.py", "import os\n", 'import os\nos.getenv("BRAND_NEW")\n')
    assert "BRAND_NEW" not in (edit_note.check(out, state) or "")
