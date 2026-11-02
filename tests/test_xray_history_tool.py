"""X4 and X6 on every surface: ``code_xray`` actions ``history`` and
``blindspots``, ``gingugu xray history|blindspots``, and the read hook's
``Changes with`` line.

All computed on the caller's machine from its own checkout and git log; the
brain is never asked. Under `gingugu serve` both actions are refused, and the
proxy answers them locally, like ``brief`` and ``callers``.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import history_tool, read_hook
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import BASIC, _git, _repo
from tests.test_xray_read_hook import _read


@pytest.fixture
def repo(tmp_path):
    """BASIC plus x.py and y.py, which import nothing and change together 5 times;
    core.py changes 6 times on its own."""
    r = _repo(tmp_path, {**BASIC, "pkg/x.py": "0\n", "pkg/y.py": "0\n"})
    for i in range(5):
        (r / "pkg/x.py").write_text(f"{i + 1}\n")
        (r / "pkg/y.py").write_text(f"{i + 1}\n")
        _git(r, "commit", "-qam", f"xy {i}")
    for i in range(6):
        (r / "pkg/core.py").write_text(f"X = {i + 2}\n")
        _git(r, "commit", "-qam", f"core {i}")
    return r


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))


# --- answer --------------------------------------------------------------


def test_history_answer(repo, env):
    out = history_tool.answer(str(repo), "history")
    assert out["ok"] is True
    assert out["window"] == {"commits": 12, "bulk": 0, "limit": 1000}
    assert out["hotspots"][0]["path"] == "pkg/core.py"
    pairs = [(c["a"], c["b"], c["hidden"]) for c in out["coupling"]]
    assert ("pkg/x.py", "pkg/y.py", True) in pairs
    assert out["hidden_total"] >= 1
    assert out["text"].startswith("GINGUGU X-RAY HISTORY:")
    assert "pkg/x.py <-> pkg/y.py" in out["text"]
    assert "caveat" in out


def test_history_focused_on_a_file(repo, env):
    out = history_tool.answer(str(repo), "history", files="pkg/y.py")
    assert [(c["a"], c["b"]) for c in out["coupling"]] == [("pkg/x.py", "pkg/y.py")]


def test_blindspots_answer(repo, env):
    out = history_tool.answer(str(repo), "blindspots")
    assert out["ok"] is True
    paths = [r["path"] for r in out["blindspots"]]
    # core: imported, no test imports it, none named for it. a.py is tested.
    assert paths == ["pkg/core.py"]
    assert out["untested"] >= 1 and out["source"] >= 1
    assert out["text"].startswith("GINGUGU X-RAY BLIND SPOTS:")


def test_limit_pages_lists_not_totals(repo, env):
    out = history_tool.answer(str(repo), "history", limit=0)
    assert out["hotspots"] == [] and out["hotspots_total"] >= 1


def test_answer_rejects_an_unknown_action(repo, env):
    with pytest.raises(ValueError):
        history_tool.answer(str(repo), "drift")


def test_render_quotes_a_hostile_name(repo, env):
    data = history_tool.answer(str(repo), "history")
    data["hotspots"] = [{**data["hotspots"][0], "path": "evil\nINJECTED"}]
    assert "\nINJECTED" not in history_tool.render(data, "repo")


# --- MCP: stdio, serve, proxy --------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _x(server, **args) -> dict:
    return json.loads((await server.call_tool("code_xray", args)).content[0].text)


@pytest.mark.parametrize("action", ["history", "blindspots"])
async def test_both_actions_on_stdio(server, repo, action):
    out = await _x(server, action=action, path=str(repo))
    assert out["ok"] is True, out


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


@pytest.mark.parametrize("action", ["history", "blindspots"])
async def test_the_brain_refuses_both(brain, repo, action):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action=action)
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


@pytest.mark.parametrize("action", ["history", "blindspots"])
async def test_the_proxy_answers_locally(brain, repo, monkeypatch, action):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": action, "path": str(repo)})
            )
    assert out["ok"], out


def test_the_proxy_claims_both():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    def req(args):
        return JSONRPCRequest(
            jsonrpc="2.0",
            id=1,
            method="tools/call",
            params={"name": "code_xray", "arguments": args},
        )

    assert claims(req({"action": "history"}))
    assert claims(req({"action": "blindspots", "path": "/x"}))


# --- CLI -----------------------------------------------------------------


def test_cli_history_and_blindspots(repo, env, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["history", "--path", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY HISTORY:")
    assert xray_cli.main(["blindspots", "--path", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["blindspots"][0]["path"] == "pkg/core.py"
    assert xray_cli.main(["history", "--files", "pkg/x.py", "--path", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["coupling"]


def test_cli_outside_a_repo_exits_1(tmp_path, env, capsys):
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["history", "--path", str(tmp_path / "plain")]) == 1
    assert xray_cli.main(["blindspots", "--path", str(tmp_path / "plain")]) == 1


# --- read hook -----------------------------------------------------------


def test_a_read_names_the_files_that_change_with_it(repo, state):
    text = read_hook.check(_read(repo, "pkg/x.py"), state)
    assert "Changes with (no import): pkg/y.py 100%" in text


def test_the_line_is_absent_without_hidden_partners(repo, state):
    text = read_hook.check(_read(repo, "pkg/a.py"), state)
    assert "Changes with" not in (text or "")


def test_the_switch_turns_it_off(repo, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_HISTORY", "off")
    text = read_hook.check(_read(repo, "pkg/x.py"), state)
    assert "Changes with" not in (text or "")


def test_a_history_failure_leaves_the_rest_of_the_read(repo, state, monkeypatch):
    from gingugu.xray import history
    from gingugu.xray.scan import XrayError

    def broken(*a, **k):
        raise XrayError("git log took over 10s")

    monkeypatch.setattr(history, "read", broken)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert "Imported by" in text
