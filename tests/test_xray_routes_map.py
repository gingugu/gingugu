"""X7b: the route map over a real checkout - X7d serves joined to X23 calls."""

from __future__ import annotations

import pytest

from gingugu.xray import route_map
from tests.test_xray_edit_hook import _repo

FILES = {
    "backend/__init__.py": "",
    "backend/app.py": (
        "from fastapi import FastAPI\n"  # 1
        "app = FastAPI()\n"  # 2
        "\n"  # 3
        "@app.get('/api/users/{user_id}')\n"  # 4
        "def user(user_id): ...\n"  # 5
        "\n"  # 6
        "@app.post('/api/users')\n"  # 7
        "def make(): ...\n"  # 8
        "\n"  # 9
        "@app.get('/api/users')\n"  # 10
        "def users(): ...\n"  # 11
    ),
    "web/src/api.ts": (
        "export const one = (id) => fetch(`/api/users/${id}`)\n"  # 1
        "export const all = () => fetch('/api/users')\n"  # 2
        "export const gone = (id) => fetch(`/api/users/${id}/avatar`)\n"  # 3
        "export const gh = 'https://github.com/api/users/7'\n"  # 4
    ),
    "tests/test_app.py": (
        "@app.get('/api/fake')\n"  # 1: a test's route serves nothing
        "def fake(): ...\n"  # 2
        "def test_users(client):\n"  # 3
        "    client.get('/api/users')\n"  # 4
    ),
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


def test_serves_come_from_x7d_and_skip_tests(repo, tmp_path):
    m = route_map.read(repo, tmp_path / "state")
    assert sorted((s.key, s.method) for s in m.serves) == [
        ("/api/users", "GET"),
        ("/api/users", "POST"),
        ("/api/users/*", "GET"),
    ]
    assert all(s.path == "backend/app.py" for s in m.serves)


def test_calls_join_and_a_foreign_host_does_not(repo, tmp_path):
    m = route_map.read(repo, tmp_path / "state")
    got = sorted((e.call.path, e.call.line, e.serve.key, e.methods) for e in m.edges)
    assert got == [
        ("tests/test_app.py", 4, "/api/users", "GET|POST"),
        ("web/src/api.ts", 1, "/api/users/*", "GET"),
        ("web/src/api.ts", 2, "/api/users", "GET|POST"),
    ]
    assert [(c.path, c.key) for c in m.unserved] == [("web/src/api.ts", "/api/users/*/avatar")]
    assert m.complete and m.unread == 0


def test_by_file_views(repo, tmp_path):
    m = route_map.read(repo, tmp_path / "state")
    served = route_map.served_in(m, "backend/app.py")
    assert sorted({e.call.path for e in served}) == ["tests/test_app.py", "web/src/api.ts"]
    made = route_map.calls_in(m, "web/src/api.ts")
    assert [e.serve.path for e in made] == ["backend/app.py", "backend/app.py"]
    assert route_map.uncalled(m) == []


POLYGLOT = {
    "server/app.ts": (
        "import express from 'express'\n"  # 1
        "const app = express()\n"  # 2
        "app.get('/api/items/:id', (req, res) => res.send('x'))\n"  # 3
    ),
    "go/main.go": (
        "package main\n"  # 1
        'import "net/http"\n'  # 2
        "func orders(w http.ResponseWriter, r *http.Request) {}\n"  # 3
        "func main() {\n"  # 4
        '\thttp.HandleFunc("/api/orders", orders)\n'  # 5
        "}\n"  # 6
    ),
    "go/main_test.go": 'package main\nfunc TestX() { http.Get("/api/items/9") }\n',
    "scripts/load.py": "import httpx\nhttpx.get('/api/orders')\n",
    "cli/client.go": 'package cli\nimport "net/http"\nfunc F() { http.Get("/api/items/7") }\n',
    "web/a.spec.ts": "it('x', () => fetch('/api/orders'))\n",
}


def test_every_language_serves_and_calls(tmp_path):
    from gingugu.xray import route_join

    repo = _repo(tmp_path, POLYGLOT)
    m = route_map.read(repo, tmp_path / "state")
    assert sorted((s.path, s.key) for s in m.serves) == [
        ("go/main.go", "/api/orders"),
        ("server/app.ts", "/api/items/*"),
    ]
    got = sorted((e.call.path, e.serve.path) for e in m.edges)
    assert got == [
        ("cli/client.go", "server/app.ts"),
        ("go/main_test.go", "server/app.ts"),
        ("scripts/load.py", "go/main.go"),
        ("web/a.spec.ts", "go/main.go"),
    ]
    assert route_join.caller_class("go/main_test.go") == "test"
    assert route_join.caller_class("web/a.spec.ts") == "test"


def test_off_switch(monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_ROUTES", "off")
    assert not route_map.enabled()
    monkeypatch.setenv("MEMORY_XRAY_ROUTES", "on")
    assert route_map.enabled()
