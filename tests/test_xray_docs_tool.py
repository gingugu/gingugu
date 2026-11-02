"""X22 ``code_xray`` action ``docs``: the docs that cite a file, asked for directly.

Answered where the checkout is - the stdio server, the proxy and the CLI - like
``history``; a central brain never sees a checkout, so it refuses.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import docs_tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import BASIC, _repo

DOCS = {
    **BASIC,
    "docs/runbook.md": "Edit pkg/core.py and pkg/a.py.\n",
    "docs/deploy.md": "pkg/core.py ships first.\n",
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, DOCS)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    monkeypatch.delenv("MEMORY_XRAY_DOCS", raising=False)


def test_named_files_list_their_docs(repo, env):
    out = docs_tool.answer(str(repo), files="pkg/core.py,pkg/leaf.py")
    assert out["ok"] is True
    assert out["files"] == [
        {
            "path": "pkg/core.py",
            "docs": [
                {"doc": "docs/deploy.md", "repo": None},
                {"doc": "docs/runbook.md", "repo": None},
            ],
        },
        {"path": "pkg/leaf.py", "docs": []},
    ]
    assert out["text"].startswith("GINGUGU X-RAY DOCS:")
    assert "pkg/core.py: docs/deploy.md, docs/runbook.md" in out["text"]
    assert "pkg/leaf.py: no doc cites it" in out["text"]


def test_no_files_is_the_most_cited(repo, env):
    out = docs_tool.answer(str(repo))
    assert out["cited"][0] == {"path": "pkg/core.py", "docs": 2}
    assert out["totals"] == {"docs": 2, "cited_files": 2, "siblings": 0}


def test_limit_pages_the_most_cited(repo, env):
    out = docs_tool.answer(str(repo), limit=1)
    assert len(out["cited"]) == 1 and out["totals"]["cited_files"] == 2


def test_the_switch_off_is_an_error(repo, env, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_DOCS", "off")
    with pytest.raises(ValueError, match="MEMORY_XRAY_DOCS"):
        docs_tool.answer(str(repo))


def test_a_hostile_doc_name_is_quoted(repo, env):
    data = docs_tool.answer(str(repo), files="pkg/core.py")
    data["files"][0]["docs"] = [{"doc": "evil\nINJECTED", "repo": None}]
    assert "\nINJECTED" not in docs_tool.render(data, "repo")


# --- MCP: stdio, serve, proxy --------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    from gingugu.server import build_server

    return build_server()


async def test_docs_on_stdio(server, repo):
    out = json.loads(
        (
            await server.call_tool(
                "code_xray", {"action": "docs", "path": str(repo), "files": "pkg/core.py"}
            )
        )
        .content[0]
        .text
    )
    assert out["ok"] is True, out
    assert out["files"][0]["path"] == "pkg/core.py"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_docs(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="docs")
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_the_proxy_answers_docs_locally(brain, repo, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(repo.parent / "no-roots"))
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "docs", "path": str(repo)})
            )
    assert out["ok"], out


def test_the_proxy_claims_docs():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    req = JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params={"name": "code_xray", "arguments": {"action": "docs"}},
    )
    assert claims(req)


# --- CLI -----------------------------------------------------------------


def test_cli_docs(repo, env, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["docs", "--path", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY DOCS:")
    assert xray_cli.main(["docs", "--files", "pkg/core.py", "--path", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["path"] == "pkg/core.py"


def test_cli_docs_outside_a_repo_exits_1(tmp_path, env):
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["docs", "--path", str(tmp_path / "plain")]) == 1
