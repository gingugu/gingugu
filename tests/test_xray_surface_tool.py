"""X7d: ``code_xray`` action ``surface`` and ``gingugu xray surface``.

Answered where the checkout is - the stdio server, the proxy and the CLI - like
``tables``; ``gingugu serve`` refuses it, since the brain never reads a path.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import surface_tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _repo

FILES = {
    "app/__init__.py": "",
    "app/server.py": (
        "import subprocess\n"  # 1
        "from app.store import Store\n"  # 2
        "\n"  # 3
        "@app.get('/run')\n"  # 4
        "def run(cmd):\n"  # 5
        "    return _go(cmd)\n"  # 6
        "\n"  # 7
        "def _go(cmd):\n"  # 8
        "    return subprocess.run(cmd)\n"  # 9
        "\n"  # 10
        "@app.get('/items')\n"  # 11
        "def items(db):\n"  # 12
        "    s = Store(db)\n"  # 13
        "    return s.all()\n"  # 14
        "\n"  # 15
        "def internal():\n"  # 16
        "    import pickle\n"  # 17
        "    return pickle.loads(b'')\n"  # 18
    ),
    "app/store.py": (
        "class Store:\n"  # 1
        "    def __init__(self, db):\n"  # 2
        "        self.db = db\n"  # 3
        "    def all(self):\n"  # 4
        "        return self.db.execute('SELECT * FROM users')\n"  # 5
    ),
    "db/schema.sql": "CREATE TABLE users (id INTEGER);\n",
    "tests/test_server.py": "@app.get('/t')\ndef t(): pass\n",
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("MEMORY_XRAY_TABLES", raising=False)


def test_every_entry_with_its_reach_most_dangerous_first(repo, env):
    out = surface_tool.answer(str(repo))
    assert out["ok"] is True
    run, items = out["entries"]  # a test file's route is not an entry
    assert (run["label"], run["handler"], run["line"]) == ("app.get", "run", 4)
    assert run["reach"] == [
        {
            "sink": "subprocess.run",
            "category": "exec",
            "path": "app/server.py",
            "symbol": "_go",
            "line": 9,
            "hops": 1,
            "via": ["run", "_go"],
        }
    ]
    assert [(r["sink"], r["path"], r["symbol"], r["hops"]) for r in items["reach"]] == [
        ("db:users", "app/store.py", "Store.all", 1)
    ]
    assert [(u["sink"], u["symbol"]) for u in out["unreached"]] == [("pickle.loads", "internal")]
    assert out["totals"] == {
        "entries": 2,
        "reaching": 2,
        "unresolved": 0,
        "sinks": 3,
        "unreached": 1,
    }
    text = out["text"]
    assert text.startswith("GINGUGU X-RAY SURFACE: repo - 2 entry points (2 reach a sink")
    assert "exec subprocess.run at app/server.py:9 (1 hop: run > _go)" in text
    assert "deserialize pickle.loads at app/server.py:18 in internal" in text


def test_files_keep_the_entries_in_them_or_reaching_into_them(repo, env):
    out = surface_tool.answer(str(repo), "app/store.py")
    assert [e["handler"] for e in out["entries"]] == ["items"]
    assert out["unreached"] == []


def test_the_table_map_off_drops_db_sinks(repo, env, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TABLES", "off")
    out = surface_tool.answer(str(repo))
    assert [e["handler"] for e in out["entries"] if e["reach"]] == ["run"]


def test_a_crafted_path_is_quoted_in_the_text():
    data = {
        "entries": [
            {
                "path": "a\nINJECTED.py",
                "line": 1,
                "label": "app.get",
                "kind": "http",
                "handler": "h",
                "inline": False,
                "resolved": True,
                "reach": [],
                "reach_total": 0,
            }
        ],
        "unreached": [],
        "totals": {"entries": 1, "reaching": 0, "unresolved": 0, "sinks": 0, "unreached": 0},
    }
    assert "\nINJECTED" not in surface_tool.render(data, "repo")


# --- MCP: stdio, serve, proxy ----------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def test_surface_on_stdio(server, repo):
    args = {"action": "surface", "path": str(repo)}
    out = json.loads((await server.call_tool("code_xray", args)).content[0].text)
    assert out["ok"] is True, out
    assert out["totals"]["entries"] == 2


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_surface(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="surface")
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_the_proxy_answers_surface_locally(brain, repo, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "surface", "path": str(repo)})
            )
    assert out["ok"], out


# --- CLI ---------------------------------------------------------------------------


def test_cli_surface(repo, env, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["surface", "--path", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY SURFACE:")
    argv = ["surface", "--files", "app/store.py", "--path", str(repo), "--json"]
    assert xray_cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["entries"][0]["handler"] == "items"


def test_cli_surface_outside_a_repo_exits_1(tmp_path, env):
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["surface", "--path", str(tmp_path / "plain")]) == 1
