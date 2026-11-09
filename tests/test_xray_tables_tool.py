"""X7c: ``code_xray`` action ``tables`` and ``gingugu xray tables``.

Answered where the checkout is - the stdio server, the proxy and the CLI - like
``env``; ``gingugu serve`` refuses it, since the brain never reads a path.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import table_tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _repo
from tests.test_xray_tables_map import FILES


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("MEMORY_XRAY_TABLES", raising=False)


def test_a_file_using_tables_names_where_each_is_defined(repo, env):
    out = table_tool.answer(str(repo), "app/repo.py")
    assert out["ok"] is True
    (f,) = out["files"]
    assert f["path"] == "app/repo.py"
    uses = {u["name"]: u for u in f["uses"]}
    assert list(uses) == ["users", "orders", "legacy_log", "ghost_table"]
    assert uses["users"]["defined_in"] == [{"path": "db/schema.sql", "line": 1}]
    assert uses["ghost_table"]["defined_in"] == []
    assert uses["legacy_log"]["dropped_in"] == [{"path": "db/migrate_002.sql", "line": 1}]
    assert uses["users"]["dropped_in"] == []
    assert f["defines"] == [] and f["drops"] == []
    text = out["text"]
    assert "uses users: defined in db/schema.sql:1" in text
    assert "uses ghost_table: defined nowhere here" in text
    assert "dropped in db/migrate_002.sql:1" in text


def test_a_file_defining_tables_names_who_uses_each(repo, env):
    (f,) = table_tool.answer(str(repo), "db/schema.sql")["files"]
    defines = {d["name"]: d for d in f["defines"]}
    assert list(defines) == ["users", "orders", "docs_fts", "legacy_log", "active_users"]
    # who else uses it: the defining file's own uses are not news
    assert [u["path"] for u in defines["users"]["used_by"]] == [
        "app/repo.py", "app/repo.py", "cmd/main.go", "tests/test_repo.py",
    ]  # fmt: skip
    assert defines["docs_fts"]["used_by"] == []


def test_a_file_dropping_tables_names_who_still_uses_each(repo, env):
    (f,) = table_tool.answer(str(repo), "db/migrate_002.sql")["files"]
    drops = {d["name"]: d for d in f["drops"]}
    assert drops["legacy_log"]["used_by"] == [{"path": "app/repo.py", "line": 11}]
    assert drops["scratch"]["used_by"] == []


def test_no_files_lists_every_table_and_flags_the_one_sided(repo, env):
    out = table_tool.answer(str(repo))
    rows = {r["name"]: r for r in out["tables"]}
    assert rows["ghost_table"]["flag"] == "defined nowhere here"
    assert rows["legacy_log"]["flag"] == "dropped, still used"
    assert rows["docs_fts"]["flag"] == "used nowhere here"
    assert rows["shipments"]["flag"] == "used nowhere here"  # created and dropped, never used
    assert rows["users"]["flag"] is None and rows["audit_log"]["flag"] is None
    assert rows["users"]["defines"] == 1 and rows["users"]["uses"] == 5
    assert rows["users"]["files"] == 4  # distinct files using it
    assert "scratch" not in rows  # only ever dropped: neither defined nor used here
    t = out["totals"]
    assert t["tables"] == len(out["tables"])
    assert (t["defined_nowhere"], t["dropped_still_used"]) == (1, 1)
    assert t["used_nowhere"] == 5  # docs_fts, active_users, invoices, payments, shipments
    assert "defined nowhere here" in out["text"]
    assert out["caveat"]


def test_one_sided_tables_come_first_worst_first(repo, env):
    names = [r["name"] for r in table_tool.answer(str(repo))["tables"]]
    assert names[:2] == ["ghost_table", "legacy_log"]
    assert set(names[2:7]) == {"docs_fts", "active_users", "invoices", "payments", "shipments"}
    assert names[7:] == ["audit_log", "orders", "users"]


def test_a_table_seen_only_in_tests_is_flagged_and_sorted_last(tmp_path, env):
    fixture = _repo(
        tmp_path,
        {
            "db/schema.sql": "CREATE TABLE t_one (id INT);\n",
            "app/a.py": 'q = "SELECT 1 FROM t_one JOIN t_gone"\n',
            "tests/test_a.py": (
                'db.execute("CREATE TABLE t_fix (id INT)")\n' 'db.execute("SELECT 1 FROM t_nope")\n'
            ),
        },
    )
    out = table_tool.answer(str(fixture))
    rows = {r["name"]: r for r in out["tables"]}
    assert rows["t_fix"]["flag"] == "tests only" and rows["t_nope"]["flag"] == "tests only"
    assert rows["t_gone"]["flag"] == "defined nowhere here"
    assert [r["name"] for r in out["tables"]] == ["t_gone", "t_one", "t_fix", "t_nope"]
    t = out["totals"]
    assert (t["defined_nowhere"], t["used_nowhere"], t["tests_only"]) == (1, 0, 2)


def test_limit_caps_the_rows(repo, env):
    assert len(table_tool.answer(str(repo), limit=2)["tables"]) == 2


def test_the_switch_off_is_an_error(repo, env, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TABLES", "off")
    with pytest.raises(ValueError, match="MEMORY_XRAY_TABLES"):
        table_tool.answer(str(repo))


def test_a_hostile_file_name_is_quoted(repo, env):
    data = table_tool.answer(str(repo), "app/repo.py")
    data["files"][0]["uses"][0]["defined_in"] = [{"path": "evil\nINJECTED", "line": 1}]
    assert "\nINJECTED" not in table_tool.render(data, "repo")


# --- MCP: stdio, serve, proxy ----------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def test_tables_on_stdio(server, repo):
    args = {"action": "tables", "path": str(repo), "files": "app/repo.py"}
    out = json.loads((await server.call_tool("code_xray", args)).content[0].text)
    assert out["ok"] is True, out
    assert out["files"][0]["path"] == "app/repo.py"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_tables(brain, repo):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="tables")
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_the_proxy_answers_tables_locally(brain, repo, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "tables", "path": str(repo)})
            )
    assert out["ok"], out


def test_the_proxy_claims_tables():
    from mcp.types import JSONRPCRequest

    from gingugu.proxy_xray import claims

    req = JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params={"name": "code_xray", "arguments": {"action": "tables"}},
    )
    assert claims(req)


# --- CLI ---------------------------------------------------------------------------


def test_cli_tables(repo, env, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["tables", "--path", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY TABLES:")
    argv = ["tables", "--files", "app/repo.py", "--path", str(repo), "--json"]
    assert xray_cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["path"] == "app/repo.py"


def test_cli_tables_outside_a_repo_exits_1(tmp_path, env):
    from gingugu import xray_cli

    (tmp_path / "plain").mkdir()
    assert xray_cli.main(["tables", "--path", str(tmp_path / "plain")]) == 1
