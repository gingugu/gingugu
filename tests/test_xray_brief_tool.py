"""X19 on the MCP surface: ``code_xray`` action ``brief``.

On stdio the server reads the checkout and answers from the hook graph cache.
Under `gingugu serve` the brain refuses: the checkout is the caller's disk. The
proxy answers ``brief`` entirely on this machine, so the brain is never asked.
"""

from __future__ import annotations

import json

import pytest

from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_brief import branched  # noqa: F401 - fixture
from tests.test_xray_edit_hook import BASIC, _repo


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _x(server, **args) -> dict:
    return json.loads((await server.call_tool("code_xray", args)).content[0].text)


async def test_brief_on_stdio_for_a_range(server, branched):  # noqa: F811
    out = await _x(server, action="brief", range="HEAD~1..HEAD", path=str(branched))
    assert out["ok"], out
    assert out["counts"] == {"A": 2, "M": 1, "D": 1, "R": 1}
    assert out["blast"]["code"] == ["pkg/a.py"]
    assert out["text"].startswith("GINGUGU X-RAY BRIEF:")
    assert "estimate" in out["caveat"]


async def test_brief_on_stdio_for_named_files_absolute_or_relative(server, repo):
    out = await _x(
        server, action="brief", files=[str(repo / "pkg/core.py"), "pkg/a.py"], path=str(repo)
    )
    assert out["ok"], out
    assert {c["path"] for c in out["changed"]} == {"pkg/core.py", "pkg/a.py"}


async def test_brief_defaults_to_the_working_tree(server, repo):
    (repo / "pkg/core.py").write_text("X = 3\n")
    out = await _x(server, action="brief", path=str(repo))
    assert [c["path"] for c in out["changed"]] == ["pkg/core.py"]


async def test_brief_refuses_range_and_files_together(server, repo):
    out = await _x(server, action="brief", range="HEAD", files="pkg/a.py", path=str(repo))
    assert out["ok"] is False and "range" in out["error"]


async def test_brief_refuses_an_option_shaped_range(server, repo):
    out = await _x(server, action="brief", range="--output=/tmp/x", path=str(repo))
    assert out["ok"] is False


async def test_brief_does_not_write_a_snapshot_to_the_brain(server, repo):
    await _x(server, action="brief", files="pkg/core.py", path=str(repo))
    stats = json.loads((await server.call_tool("memory_stats", {})).content[0].text)
    over = await _x(server, action="overview", fingerprint="0" * 64)
    assert stats["ok"]
    assert over.get("missing_snapshot") is True  # nothing was ingested by brief


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_brief(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="brief", files="pkg/a.py")
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_brief_through_the_proxy_never_asks_the_brain(
    brain, branched, monkeypatch  # noqa: F811
):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool(
                    "code_xray",
                    {"action": "brief", "range": "HEAD~1..HEAD", "path": str(branched)},
                )
            )
    assert out["ok"], out
    assert out["blast"]["tests"] == ["tests/test_a.py"]
    assert out["text"].startswith("GINGUGU X-RAY BRIEF:")


def test_a_non_utf8_name_still_serialises_on_stdio(repo, tmp_path, monkeypatch):
    """git hands back raw bytes; a lone surrogate must not reach the JSON encoder."""
    from pydantic_core import to_json

    from gingugu.xray import brief, hook_common

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    g = hook_common.graph(repo, tmp_path / "hook-sessions")
    bad = b"pkg/bad\xff.py".decode("utf-8", "surrogateescape")
    data = brief.build(g, [brief.Change("R", bad, bad)])
    to_json(data)  # raises on a lone surrogate
    assert "�" in data["changed"][0]["path"]


def test_a_dot_file_name_is_not_a_change(repo, tmp_path, monkeypatch):
    from gingugu.xray import brief

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    out = brief.answer(str(repo), None, ".,pkg/a.py")
    assert [c["path"] for c in out["changed"]] == ["pkg/a.py"]


def test_limit_is_clamped(repo, tmp_path, monkeypatch):
    from gingugu.xray import brief

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    seen = {}
    real = brief.build

    def spy(g, changes, limit=10):
        seen["limit"] = limit
        return real(g, changes, limit)

    monkeypatch.setattr(brief, "build", spy)
    brief.answer(str(repo), None, "pkg/core.py", limit=10_000)
    assert seen["limit"] == brief.MAX_LIMIT


def test_cli_turns_any_failure_into_exit_1(repo, tmp_path, monkeypatch, capsys):
    from gingugu import xray_cli
    from gingugu.xray import brief

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(brief, "answer", boom)
    assert xray_cli.main(["brief", "--path", str(repo)]) == 1
    assert "disk on fire" in capsys.readouterr().err


def test_the_proxy_claims_brief_with_or_without_a_path():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    def req(args):
        return JSONRPCRequest(
            jsonrpc="2.0",
            id=1,
            method="tools/call",
            params={"name": "code_xray", "arguments": args},
        )

    assert claims(req({"action": "brief"}))
    assert claims(req({"action": "brief", "path": "/x", "range": "a..b"}))
