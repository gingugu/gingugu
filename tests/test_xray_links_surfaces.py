"""X23 ``links`` on every surface: stdio, serve (refused), the proxy, the CLI, the brief.

The ecosystem is ``test_xray_links_tool``'s: a gateway, a client that calls it,
a sidecar citing it, a portal pinning the client.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import brief
from gingugu.xray.links import render, tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _git
from tests.test_xray_links_tool import eco  # noqa: F401 - the fixture

# --- stdio, serve, proxy -----------------------------------------------------------


@pytest.fixture
def server(eco, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEMORY_NAMESPACE", "gateway")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def test_links_on_stdio(server, eco):  # noqa: F811
    result = await server.call_tool(
        "code_xray", {"action": "links", "path": str(eco), "files": "guardrails/policy.py"}
    )
    out = json.loads(result.content[0].text)
    assert out["ok"] is True, out
    assert {r["repo"] for r in out["ripple"]} == {"acme/ledger", "acme/sidecar", "acme/portal"}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_links(brain):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="links")
    assert out["ok"] is False and "gingugu serve" in out["error"]


async def test_the_proxy_answers_links_locally(brain, eco, monkeypatch):  # noqa: F811
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "links", "path": str(eco)})
            )
    assert out["ok"], out
    assert out["leaning_on_this_total"] == 5


def test_the_proxy_claims_links():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    req = JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params={"name": "code_xray", "arguments": {"action": "links"}},
    )
    assert claims(req)


# --- CLI ---------------------------------------------------------------------------------


def test_cli_links(eco, capsys):  # noqa: F811
    from gingugu import xray_cli

    assert xray_cli.main(["links", "--path", str(eco)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY LINKS: acme/gateway")
    args = ["links", "--files", "guardrails/policy.py", "--path", str(eco), "--json"]
    assert xray_cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["files"] == ["guardrails/policy.py"]


def test_cli_links_warm_fills_every_cache_and_summary(eco, tmp_path, capsys):  # noqa: F811
    from gingugu import xray_cli
    from gingugu.xray.links import inbound

    assert xray_cli.main(["links", "--warm"]) == 0  # no path: the first checkout under the roots
    assert capsys.readouterr().out.startswith("warmed 4 checkouts")
    assert inbound.map_line(eco, tmp_path / "hook-sessions")


def test_cli_links_outside_a_repo_exits_1(tmp_path, eco):  # noqa: F811
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["links", "--path", str(tmp_path / "plain")]) == 1


# --- the brief ----------------------------------------------------------------------------


def test_the_brief_names_the_repos_leaning_on_the_change(eco):  # noqa: F811
    (eco / "guardrails/policy.py").write_text("LIMIT = 4\n")
    out = brief.answer(str(eco), None, None)
    assert out["links"]["files"] == ["guardrails/policy.py"]
    assert "Other repos leaning on these files" in out["text"]
    assert "acme/ledger src/hook.ts:1 -> guardrails/resolver.py" in out["text"]
    assert "hop 2: acme/portal" in out["text"]


def test_a_brief_over_a_range_names_them_too(eco):  # noqa: F811
    (eco / "guardrails/resolver.py").write_text("# moved\n")
    _git(eco, "commit", "-qam", "change")
    out = brief.answer(str(eco), "HEAD~1..HEAD", None)
    assert {r["from"] for r in out["links"]["leaning_on_this"]} == {"acme/ledger", "acme/sidecar"}


def test_links_never_cost_the_brief(eco, monkeypatch):  # noqa: F811
    from gingugu.xray.links import index

    def boom(*a, **k):
        raise RuntimeError("index down")

    monkeypatch.setattr(index, "read", boom)
    (eco / "guardrails/policy.py").write_text("LIMIT = 5\n")
    out = brief.answer(str(eco), None, None)
    assert out["ok"] is True and out["links"] is None
    assert "Other repos" not in out["text"]


def test_the_brief_without_the_switch_has_no_links(eco, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEMORY_XRAY_LINKS", "off")
    (eco / "guardrails/policy.py").write_text("LIMIT = 6\n")
    assert brief.answer(str(eco), None, None)["links"] is None


# --- hostile names ------------------------------------------------------------------------


def test_hostile_names_are_quoted_and_cut_in_the_text(eco):  # noqa: F811
    data = tool.answer(str(eco))
    evil = data["leaning_on_this"][0] | {
        "from": "evil\nINJECTED", "file": "x" * 5000, "name": "n\r\nX", "detail": "d" * 500,
    }  # fmt: skip
    data["leaning_on_this"] = [evil]
    text = render.text(data)
    assert "\nINJECTED" not in text and "\r\nX" not in text and len(text) < 7000
