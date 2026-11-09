"""X7a: ``code_xray`` action ``env`` and ``gingugu xray env``.

Answered where the checkout is - the stdio server, the proxy and the CLI - like
``docs``; ``gingugu serve`` refuses it, since the brain never reads a path.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import env_tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _repo
from tests.test_xray_env_map import FILES


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("MEMORY_XRAY_ENV", raising=False)


def test_named_files_list_what_they_read_and_where_it_is_set(repo, env):
    out = env_tool.answer(str(repo), "app/settings.py,Dockerfile")
    assert out["ok"] is True
    settings, docker = out["files"]
    assert settings["path"] == "app/settings.py"
    reads = {r["name"]: r for r in settings["reads"]}
    assert reads["DB_URL"]["set_in"] == [{"path": "docker-compose.yml", "line": 5}]
    assert reads["APP_DEBUG"]["set_in"] == [{"path": ".github/workflows/ci.yml", "line": 4}]
    assert settings["sets"] == []
    sets = {s["name"]: s for s in docker["sets"]}
    assert sets["GO_PORT"]["read_by"] == [{"path": "cmd/main.go", "line": 3}]
    assert sets["BUILD_REV"]["read_by"] == []
    assert "DB_URL" in out["text"] and "docker-compose.yml:5" in out["text"]


def test_no_files_lists_every_name_and_flags_the_one_sided(repo, env):
    out = env_tool.answer(str(repo))
    rows = {r["name"]: r for r in out["names"]}
    assert rows["GO_HOST"]["flag"] == "never set here"
    assert rows["SCRIPT_READ"]["flag"] == "never set here"
    assert rows["UNUSED_SET"]["flag"] == "never read here"
    assert rows["BUILD_REV"]["flag"] == "never read here"
    assert rows["DB_URL"]["flag"] is None
    assert rows["RUN_MODE"]["reads"] == 1 and rows["RUN_MODE"]["sets"] == 2
    assert out["totals"]["names"] == len(out["names"])
    assert "never set here" in out["text"]
    assert out["caveat"]


def test_one_sided_names_come_first(repo, env):
    names = [r["name"] for r in env_tool.answer(str(repo))["names"]]
    flagged = [n for n in names if n in ("GO_HOST", "SCRIPT_READ", "UNUSED_SET", "BUILD_REV")]
    assert names[: len(flagged)] == flagged


def test_limit_caps_the_rows(repo, env):
    assert len(env_tool.answer(str(repo), limit=2)["names"]) == 2


def test_the_switch_off_is_an_error(repo, env, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_ENV", "off")
    with pytest.raises(ValueError, match="MEMORY_XRAY_ENV"):
        env_tool.answer(str(repo))


def test_a_hostile_file_name_is_quoted(repo, env):
    data = env_tool.answer(str(repo), "app/settings.py")
    data["files"][0]["reads"][0]["set_in"] = [{"path": "evil\nINJECTED", "line": 1}]
    assert "\nINJECTED" not in env_tool.render(data, "repo")


# --- MCP: stdio, serve, proxy ----------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def test_env_on_stdio(server, repo):
    out = json.loads(
        (
            await server.call_tool(
                "code_xray", {"action": "env", "path": str(repo), "files": "app/settings.py"}
            )
        )
        .content[0]
        .text
    )
    assert out["ok"] is True, out
    assert out["files"][0]["path"] == "app/settings.py"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_env(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="env")
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_the_proxy_answers_env_locally(brain, repo, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "env", "path": str(repo)})
            )
    assert out["ok"], out


def test_the_proxy_claims_env():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    req = JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params={"name": "code_xray", "arguments": {"action": "env"}},
    )
    assert claims(req)


# --- CLI ---------------------------------------------------------------------------


def test_cli_env(repo, env, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["env", "--path", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY ENV:")
    assert xray_cli.main(["env", "--files", "app/settings.py", "--path", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["path"] == "app/settings.py"


def test_cli_env_outside_a_repo_exits_1(tmp_path, env):
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["env", "--path", str(tmp_path / "plain")]) == 1
