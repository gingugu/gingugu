"""X7b in the hooks: a file's first read names the routes it serves, with who calls
each, and the calls it makes, with what serves each. An edit says so when it adds
a call served nowhere here, and when it stops serving a route other files still
call. ``MEMORY_XRAY_ROUTES=off`` silences all of it.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_note, read_hook, route_hook
from tests.test_xray_edit_hook import _payload, _repo
from tests.test_xray_routes_map import FILES


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_ROUTES", raising=False)
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


def _routes(text):
    return next(ln for ln in (text or "").splitlines() if ln.startswith("Routes: "))


def _route_lines(text):
    return [ln for ln in (text or "").splitlines() if ln.startswith("- Routes: ")]


# --- the read hook -------------------------------------------------------------------


def test_a_serving_file_names_who_calls_each_route(repo, state):
    line = _routes(read_hook.check(_read(repo, "backend/app.py"), state))
    assert line.startswith("Routes: serves ")
    assert "GET /api/users/* (called by web/src/api.ts:1)" in line
    assert "GET|POST /api/users (called by web/src/api.ts:2, tests/test_app.py:4)" in line


def test_a_calling_file_names_what_serves_each_call(repo, state):
    line = _routes(read_hook.check(_read(repo, "web/src/api.ts"), state))
    assert "calls " in line
    assert "/api/users (served by backend/app.py:7)" in line
    assert "/api/users/*/avatar (served nowhere here)" in line
    assert "github.com" not in line


def test_the_routes_line_is_once_per_file(repo, state):
    assert read_hook.check(_read(repo, "backend/app.py"), state)
    assert read_hook.check(_read(repo, "backend/app.py"), state) is None


def test_a_file_with_no_routes_has_no_routes_line(repo, state):
    text = read_hook.check(_read(repo, "backend/__init__.py"), state) or ""
    assert "Routes: " not in text


def test_more_than_four_items_point_at_the_tool(tmp_path, state):
    files = dict(FILES)
    files["backend/more.py"] = "".join(
        f"@app.get('/api/thing{i}')\ndef t{i}(): ...\n" for i in range(6)
    )
    repo = _repo(tmp_path, files)
    line = route_hook.file_line(repo, "backend/more.py", state)
    assert "(+2 more; code_xray routes has them all)" in line


def test_the_read_hook_stays_silent_on_a_partial_map(repo, state, monkeypatch):
    from gingugu.xray import route_map

    real = route_map.read

    def partial(*a, **k):
        return real(*a, **k)._replace(complete=False, unread=3)

    monkeypatch.setattr(route_map, "read", partial)
    assert route_hook.file_line(repo, "web/src/api.ts", state) is None


# --- the edit note -------------------------------------------------------------------


def test_an_edit_adding_a_call_served_nowhere_says_so_once(repo, state):
    old = "export const all = () => fetch('/api/users')\n"
    new = old + "export const bad = () => fetch('/api/users/me/settingz')\n"
    lines = _route_lines(edit_note.check(_edited(repo, "web/src/api.ts", old, new), state))
    assert lines == [
        "- Routes: calls /api/users/me/settingz, served nowhere here "
        "(a typo, or a route not built yet?)."
    ]
    again = "export const bad2 = () => fetch('/api/users/me/settingz')\n"
    text = edit_note.check(_edited(repo, "web/src/api.ts", new, new + again), state)
    assert _route_lines(text) == []  # once per route per session


def test_an_edit_adding_a_served_call_is_quiet(repo, state):
    old = "export const all = () => fetch('/api/users')\n"
    new = old + "export const more = () => fetch('/api/users')\n"
    assert _route_lines(edit_note.check(_edited(repo, "web/src/api.ts", old, new), state)) == []


def test_a_test_adding_an_unserved_call_is_quiet(repo, state):
    old = "    client.get('/api/users')\n"
    new = old + "    client.get('/api/users/me/settingz')\n"
    text = edit_note.check(_edited(repo, "tests/test_app.py", old, new), state)
    assert _route_lines(text) == []  # a test probing a 404 is not a typo


def test_an_edit_removing_a_called_route_names_who_still_calls_it(repo, state):
    read_hook.check(_read(repo, "backend/app.py"), state)  # the caches as they were
    old = "@app.get('/api/users/{user_id}')\ndef user(user_id): ...\n"
    lines = _route_lines(edit_note.check(_edited(repo, "backend/app.py", old, ""), state))
    assert lines == [
        "- Routes: no longer serves GET /api/users/* - still called by web/src/api.ts:1."
    ]


def test_renaming_an_uncalled_route_is_quiet(tmp_path, state):
    files = dict(FILES)
    files["backend/admin.py"] = "@app.get('/api/admin/stats')\ndef stats(): ...\n"
    repo = _repo(tmp_path, files)
    read_hook.check(_read(repo, "backend/admin.py"), state)
    text = edit_note.check(
        _edited(repo, "backend/admin.py", "/api/admin/stats", "/api/admin/statistics"), state
    )
    assert _route_lines(text) == []


def test_an_untrusted_map_never_says_served_nowhere(tmp_path, state):
    # more unserved calls from code than joined ones: X7d is missing routes here
    files = dict(FILES)
    files["web/src/more.ts"] = "".join(
        f"export const x{i} = () => fetch('/api/users/me/thing{i}')\n" for i in range(4)
    )
    repo = _repo(tmp_path, files)
    old = "export const all = () => fetch('/api/users')\n"
    new = old + "export const bad = () => fetch('/api/users/me/settingz')\n"
    text = edit_note.check(_edited(repo, "web/src/api.ts", old, new), state)
    assert _route_lines(text) == []


def test_the_switch_off_silences_all_of_it(repo, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_ROUTES", "off")
    text = read_hook.check(_read(repo, "backend/app.py"), state) or ""
    assert "Routes: " not in text
    old = "export const all = () => fetch('/api/users')\n"
    new = old + "export const bad = () => fetch('/api/users/me/settingz')\n"
    assert _route_lines(edit_note.check(_edited(repo, "web/src/api.ts", old, new), state)) == []
