"""X7d in the hooks (``surface_hook``): a file's first read names its entry points
with the worst sink each reaches, and its sinks that entry points elsewhere reach -
every category, database included, and tooling under dot-directories included. An
edit is diffed before and after: a sink an entry point can newly reach, a new
entry point, and a definition already on an entry-to-sink path are each named once.
When the after-edit rebuild cannot run, the note says the check was skipped.
``MEMORY_XRAY_SURFACE=off`` silences all of it.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_note, hook_common, read_hook
from gingugu.xray.scan import XrayError
from tests.test_xray_edit_hook import _payload, _repo

FILES = {
    "app/__init__.py": "",
    "app/server.py": (
        "from app.store import Store\n"  # 1
        "from app import jobs\n"  # 2
        "\n"  # 3
        "@app.post('/run')\n"  # 4
        "def run(cmd):\n"  # 5
        "    return jobs.go(cmd)\n"  # 6
        "\n"  # 7
        "@app.get('/items')\n"  # 8
        "def items(db):\n"  # 9
        "    s = Store(db)\n"  # 10
        "    return s.all()\n"  # 11
    ),
    "app/jobs.py": (
        "import subprocess\n"  # 1
        "def go(cmd):\n"  # 2
        "    return subprocess.run(cmd)\n"  # 3
        "def helper(x):\n"  # 4
        "    return x\n"  # 5
    ),
    "app/store.py": (
        "class Store:\n"  # 1
        "    def __init__(self, db):\n"  # 2
        "        self.db = db\n"  # 3
        "    def all(self):\n"  # 4
        "        return self.db.execute('SELECT * FROM users')\n"  # 5
    ),
    "db/schema.sql": "CREATE TABLE users (id INTEGER);\n",
    "web/api.ts": (
        'import { exec } from "child_process";\n'  # 1
        'app.get("/ts", (req, res) => {\n'  # 2
        "  exec(req.q);\n"  # 3
        "});\n"  # 4
    ),
    ".claude/hooks/guard.py": (
        "import subprocess\n"  # 1
        "if __name__ == '__main__':\n"  # 2
        "    subprocess.run(['git', 'status'])\n"  # 3
    ),
    "README.md": "# hi\n",
}


@pytest.fixture
def state(tmp_path, monkeypatch):
    for var in ("MEMORY_XRAY_SURFACE", "MEMORY_XRAY_TABLES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


def _read(repo, rel, session="cc-1"):
    out = _payload(repo, rel, tool="Read", session=session)
    out["hook_event_name"] = "PostToolUse"
    return out


def _edited(repo, rel, old, new, session="cc-1"):
    path = repo / rel
    path.write_text(path.read_text().replace(old, new))
    out = _payload(repo, rel, tool="Edit", session=session)
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"].update(old_string=old, new_string=new)
    return out


def _surface(text) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.lstrip("- ").startswith("Surface:")]


# --- the read hook -------------------------------------------------------------------


def test_a_first_read_names_its_entry_points_most_dangerous_first(repo, state):
    (line,) = _surface(read_hook.check(_read(repo, "app/server.py"), state))
    assert line.startswith("Surface: entry points here: http POST /run (L4) -> exec subprocess.run")
    assert "http GET /items (L8) -> db db:users, 1 hop" in line  # database reach speaks too
    assert line.index("POST /run") < line.index("GET /items")
    assert _surface(read_hook.check(_read(repo, "app/server.py"), state)) == []  # once


def test_a_sink_file_names_the_entry_points_that_reach_it(repo, state):
    (line,) = _surface(read_hook.check(_read(repo, "app/jobs.py"), state))
    assert line == (
        "Surface: sinks here reached from entry points: exec subprocess.run (L3, `go`) "
        "from 1 entry: http POST /run."
    )


def test_database_reach_is_named_at_the_sink(repo, state):
    (line,) = _surface(read_hook.check(_read(repo, "app/store.py"), state))
    assert "db db:users (L5, `Store.all`) from 1 entry: http GET /items" in line


def test_every_language_speaks(repo, state):
    (line,) = _surface(read_hook.check(_read(repo, "web/api.ts"), state))
    assert "http GET /ts (L2) -> exec child_process.exec, in the handler" in line


def test_tooling_under_a_dot_directory_is_surface_too(repo, state):
    (line,) = _surface(read_hook.check(_read(repo, ".claude/hooks/guard.py"), state))
    assert "main __main__ (L2) -> exec subprocess.run, 1 hop" not in line  # inline: in the handler
    assert "main __main__ (L2) -> exec subprocess.run, in the handler" in line


def test_a_file_outside_the_call_graph_says_nothing(repo, state):
    assert _surface(read_hook.check(_read(repo, "README.md"), state)) == []


def test_more_entries_than_shown_point_at_the_tool(tmp_path, state):
    routes = "".join(
        f"@app.get('/r{i}')\ndef r{i}():\n    return subprocess.run(['x'])\n" for i in range(5)
    )
    repo = _repo(tmp_path, {"app.py": "import subprocess\n" + routes})
    (line,) = _surface(read_hook.check(_read(repo, "app.py"), state))
    assert line.count(" -> exec subprocess.run") == 3
    assert line.endswith("(+2 more; code_xray surface has them all).")


def test_off_silences_the_read_lines(repo, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_SURFACE", "off")
    assert _surface(read_hook.check(_read(repo, "app/server.py"), state)) == []


# --- the edit note -------------------------------------------------------------------


def _warm(repo, state, *rels):
    for rel in rels or ("app/server.py",):
        read_hook.check(_read(repo, rel), state)


def test_an_edit_that_makes_a_sink_newly_reachable_names_it(repo, state):
    _warm(repo, state)
    text = edit_note.check(
        _edited(repo, "app/server.py", "    return s.all()\n", "    return jobs.go(s.all())\n"),
        state,
    )
    assert (
        "- Surface: this edit makes exec subprocess.run (app/jobs.py:3, `go`) reachable from "
        "http GET /items (1 hop: items > go)."
    ) in _surface(text)


def test_new_database_reach_is_named_too(repo, state):
    _warm(repo, state)
    text = edit_note.check(
        _edited(
            repo,
            "app/server.py",
            "    return jobs.go(cmd)\n",
            "    s = Store(cmd)\n    return s.all()\n",
        ),
        state,
    )
    assert any(
        ln.startswith(
            "- Surface: this edit makes db db:users (app/store.py:5, `Store.all`) "
            "reachable from http POST /run"
        )
        for ln in _surface(text)
    )


def test_an_edit_that_adds_an_entry_point_names_it(repo, state):
    _warm(repo, state)
    new = "\n@app.delete('/nuke')\ndef nuke():\n    return jobs.go(['rm'])\n"
    text = edit_note.check(
        _edited(repo, "app/server.py", "    return s.all()\n", "    return s.all()\n" + new), state
    )
    assert (
        "- Surface: this edit adds entry point http DELETE /nuke (L13) -> "
        "exec subprocess.run, 1 hop."
    ) in _surface(text)


def test_an_edit_on_a_path_from_an_entry_to_a_sink_says_so(repo, state):
    _warm(repo, state, "app/jobs.py")
    text = edit_note.check(
        _edited(
            repo,
            "app/jobs.py",
            "    return subprocess.run(cmd)\n",
            "    cmd = list(cmd)\n    return subprocess.run(cmd)\n",
        ),
        state,
    )
    assert (
        "- Surface: `go` is on a path from http POST /run to exec subprocess.run (app/jobs.py:4)."
        in _surface(text)
    )


def test_an_edit_off_every_path_says_nothing_about_the_surface(repo, state):
    _warm(repo, state, "app/jobs.py")
    text = edit_note.check(
        _edited(repo, "app/jobs.py", "    return x\n", "    return x + 1\n"), state
    )
    assert _surface(text) == []


def test_each_fact_once_per_session(repo, state):
    _warm(repo, state, "app/jobs.py")
    first = edit_note.check(
        _edited(
            repo,
            "app/jobs.py",
            "    return subprocess.run(cmd)\n",
            "    return subprocess.run(cmd )\n",
        ),
        state,
    )
    again = edit_note.check(
        _edited(
            repo,
            "app/jobs.py",
            "    return subprocess.run(cmd )\n",
            "    return subprocess.run(cmd)\n",
        ),
        state,
    )
    assert _surface(first) and _surface(again) == []


def test_a_rebuild_that_cannot_run_is_said_not_swallowed(repo, state, monkeypatch):
    _warm(repo, state, "app/jobs.py")

    def boom(*a, **k):
        raise XrayError("a graph build for this checkout did not finish this session")

    monkeypatch.setattr(hook_common, "symbols", boom)
    text = edit_note.check(
        _edited(repo, "app/jobs.py", "    return x\n", "    return x + 1\n"), state
    )
    assert _surface(text) == [
        "- Surface: the after-edit reach check was skipped (the graph rebuild did not "
        "finish); code_xray surface rebuilds it."
    ]


def test_off_silences_the_edit_lines(repo, state, monkeypatch):
    _warm(repo, state)
    monkeypatch.setenv("MEMORY_XRAY_SURFACE", "off")
    text = edit_note.check(
        _edited(repo, "app/server.py", "    return s.all()\n", "    return jobs.go(s.all())\n"),
        state,
    )
    assert _surface(text) == []
