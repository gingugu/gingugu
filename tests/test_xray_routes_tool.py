"""X7b: ``code_xray`` action ``routes`` and ``gingugu xray routes``.

Answered where the checkout is - the stdio server, the proxy and the CLI - like
``tables``; ``gingugu serve`` refuses it, since the brain never reads a path.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import route_tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _repo
from tests.test_xray_routes_map import FILES


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("MEMORY_XRAY_ROUTES", raising=False)


# --- named files -------------------------------------------------------------------


def test_a_serving_file_names_who_calls_each_route(repo, env):
    data = route_tool.answer(str(repo), "backend/app.py")
    f = data["files"][0]
    assert f["path"] == "backend/app.py"
    by_route = {s["route"]: s for s in f["serves"]}
    assert set(by_route) == {"GET|POST /api/users", "GET /api/users/*"}
    callers = by_route["GET|POST /api/users"]["called_by"]
    assert [c["path"] for c in callers] == ["web/src/api.ts", "tests/test_app.py"]  # code first
    assert by_route["GET|POST /api/users"]["line"] == 7
    text = data["text"]
    assert text.startswith("GINGUGU X-RAY ROUTES: repo - routes of 1 named files")
    assert "  serves GET|POST /api/users: called by web/src/api.ts:2, tests/test_app.py:4" in text
    assert "  serves GET /api/users/*: called by web/src/api.ts:1" in text


def test_a_calling_file_names_what_serves_each_call(repo, env):
    data = route_tool.answer(str(repo), ["web/src/api.ts"])
    f = data["files"][0]
    calls = {c["route"]: c for c in f["calls"]}
    assert calls["/api/users"]["served_by"] == [
        {"path": "backend/app.py", "line": 7, "methods": "GET|POST"}
    ]
    assert calls["/api/users"]["ambiguous"] is False
    assert [u["route"] for u in f["unserved"]] == ["/api/users/*/avatar"]
    text = data["text"]
    assert "  calls /api/users: served by backend/app.py:7 (GET|POST)" in text
    assert "  calls /api/users/*/avatar: served nowhere here" in text
    assert "github.com" not in text and "/api/users/7" not in text  # another host's path


def test_a_file_with_no_routes_says_none(repo, env):
    data = route_tool.answer(str(repo), "backend/__init__.py")
    assert data["files"][0]["serves"] == data["files"][0]["calls"] == []
    assert "- backend/__init__.py: none" in data["text"]


# --- the whole repo ------------------------------------------------------------------


def test_no_files_lists_unserved_first_then_routes_by_callers(repo, env):
    data = route_tool.answer(str(repo))
    t = data["totals"]
    assert t == {
        "served": 3,  # (file, route, method) entries
        "routes": 2,  # distinct routes after merging methods
        "edges": 3,
        "code_edges": 2,
        "unserved": 1,
        "uncalled": 0,
        "complete": True,
    }
    assert [u["route"] for u in data["unserved"]] == ["/api/users/*/avatar"]
    assert data["routes"][0]["route"] == "GET|POST /api/users"  # 2 caller files beats 1
    assert data["routes"][0]["files"] == 2
    text = data["text"]
    assert text.startswith(
        "GINGUGU X-RAY ROUTES: repo - 2 routes served, 3 calls joined (2 from code), "
        "1 served nowhere here, 0 with no caller here."
    )
    assert "- calls /api/users/*/avatar (web/src/api.ts:3): served nowhere here" in text
    assert "- GET|POST /api/users (backend/app.py:7): called from 2 files" in text


def test_limit_caps_the_rows(repo, env):
    assert len(route_tool.answer(str(repo), limit=1)["routes"]) == 1


def test_the_switch_off_is_an_error(repo, env, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_ROUTES", "off")
    with pytest.raises(ValueError, match="MEMORY_XRAY_ROUTES"):
        route_tool.answer(str(repo))


def test_a_hostile_file_name_is_quoted(repo, env):
    data = route_tool.answer(str(repo), "backend/app.py")
    data["files"][0]["serves"][0]["called_by"] = [{"path": "evil\nINJECTED", "line": 1}]
    assert "\nINJECTED" not in route_tool.render(data, "repo")


def test_a_hostile_route_is_quoted(repo, env):
    data = route_tool.answer(str(repo), "web/src/api.ts")
    data["files"][0]["calls"][0]["route"] = "/x\nINJECTED"
    assert "\nINJECTED" not in route_tool.render(data, "repo")


# --- MCP: stdio, serve, proxy ----------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def test_routes_on_stdio(server, repo):
    args = {"action": "routes", "path": str(repo), "files": "backend/app.py"}
    out = json.loads((await server.call_tool("code_xray", args)).content[0].text)
    assert out["ok"] is True, out
    assert out["files"][0]["path"] == "backend/app.py"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_routes(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="routes")
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_the_proxy_answers_routes_locally(brain, repo, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "routes", "path": str(repo)})
            )
    assert out["ok"], out


def test_the_proxy_claims_routes():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    req = JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params={"name": "code_xray", "arguments": {"action": "routes"}},
    )
    assert claims(req)


# --- CLI ---------------------------------------------------------------------------


def test_cli_routes(repo, env, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["routes", "--path", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY ROUTES:")
    argv = ["routes", "--files", "backend/app.py", "--path", str(repo), "--json"]
    assert xray_cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["path"] == "backend/app.py"


def test_cli_routes_outside_a_repo_exits_1(tmp_path, env):
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["routes", "--path", str(tmp_path / "plain")]) == 1
