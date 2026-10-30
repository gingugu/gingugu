"""X17 on the MCP surface: ``code_xray`` action ``callers``.

One symbol in one file: its direct callers and everything that reaches it by
call, from the hook symbol cache on the caller's machine. Under `gingugu serve`
the brain refuses (the checkout is the caller's disk); the proxy answers it
entirely locally, like ``brief``.
"""

from __future__ import annotations

import json

import pytest

from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _repo
from tests.test_xray_symbols_wiring import SYM


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, SYM)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _x(server, **args) -> dict:
    return json.loads((await server.call_tool("code_xray", args)).content[0].text)


async def test_callers_on_stdio(server, repo):
    out = await _x(server, action="callers", files="pkg/core.py", symbol="f", path=str(repo))
    assert out["ok"], out
    assert {(d["path"], d["symbol"]) for d in out["direct"]} == {
        ("pkg/svc.py", "serve"),
        ("pkg/core.py", "Store.save"),
    }
    assert "tests/test_svc.py" in out["reach_files"]
    assert set(out["coverage"]) == {"resolved", "external", "unresolved"}
    assert "lower bound" in out["caveat"]


async def test_callers_accepts_an_absolute_file_path(server, repo):
    out = await _x(
        server,
        action="callers",
        files=str(repo / "pkg/core.py"),
        symbol="Store.save",
        path=str(repo),
    )
    assert out["ok"], out
    assert out["direct"] == [{"path": "pkg/svc.py", "symbol": "keep", "sites": 1}]


async def test_callers_pages_with_limit_and_offset(server, repo):
    out = await _x(
        server, action="callers", files="pkg/core.py", symbol="f", path=str(repo), limit=1
    )
    assert len(out["reach"]) == 1 and out["reach_total"] == 5


async def test_an_unknown_symbol_offers_the_names_it_might_mean(server, repo):
    out = await _x(server, action="callers", files="pkg/core.py", symbol="save", path=str(repo))
    assert out["ok"] is False
    assert "save" in out["error"]
    assert out["candidates"] == ["Store.save"]


@pytest.mark.parametrize(
    "args",
    [
        {"files": "pkg/core.py"},
        {"symbol": "f"},
        {"files": "pkg/core.py,pkg/svc.py", "symbol": "f"},
    ],
)
async def test_callers_needs_exactly_one_file_and_a_symbol(server, repo, args):
    out = await _x(server, action="callers", path=str(repo), **args)
    assert out["ok"] is False


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_callers(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(
            served, token, "code_xray", action="callers", files="pkg/core.py", symbol="f"
        )
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_callers_through_the_proxy_never_asks_the_brain(brain, repo, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    args = {"action": "callers", "files": "pkg/core.py", "symbol": "f", "path": str(repo)}
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(await session.call_tool("code_xray", args))
    assert out["ok"], out
    assert out["direct_total"] == 2
