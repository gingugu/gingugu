"""The ``code_xray`` MCP tool on a local stdio server: auto-refresh, blast, overview."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    files = {
        "pkg/__init__.py": "",
        "pkg/core.py": "",
        "pkg/svc.py": "from pkg import core\n",
        "pkg/orphan.py": "",
        "app.py": "from pkg import svc\nif __name__ == '__main__':\n    pass\n",
        "tests/test_svc.py": "from pkg import svc\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    return repo


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _x(server, **args) -> dict:
    return _payload(await server.call_tool("code_xray", args))


async def test_blast_builds_the_snapshot_on_first_use(server, tmp_path):
    repo = _repo(tmp_path)
    out = await _x(server, action="blast", files="pkg/core.py", path=str(repo))
    assert out["ok"], out
    assert out["freshness"] == "current"
    assert {d["path"]: d["hops"] for d in out["code"]} == {"pkg/svc.py": 1, "app.py": 2}
    assert [d["path"] for d in out["tests"]] == ["tests/test_svc.py"]
    assert "estimate" in out["caveat"]


async def test_absolute_paths_are_made_repo_relative(server, tmp_path):
    repo = _repo(tmp_path)
    out = await _x(server, action="blast", files=[str(repo / "pkg/core.py")], path=str(repo))
    assert out["unknown_seeds"] == []
    assert out["code_total"] == 2


async def test_an_unchanged_checkout_reuses_its_snapshot_and_an_edit_makes_a_new_one(
    server, tmp_path
):
    repo = _repo(tmp_path)
    first = await _x(server, action="overview", path=str(repo))
    again = await _x(server, action="overview", path=str(repo))
    assert first["snapshot"]["snapshot_id"] == again["snapshot"]["snapshot_id"]

    (repo / "pkg/orphan.py").write_text("from pkg import core\n")  # uncommitted edit
    edited = await _x(server, action="overview", path=str(repo))
    assert edited["snapshot"]["snapshot_id"] != first["snapshot"]["snapshot_id"]
    assert edited["snapshot"]["commit"] == first["snapshot"]["commit"]


async def test_overview_ranks_and_lists_dead_file_candidates(server, tmp_path):
    repo = _repo(tmp_path)
    out = await _x(server, action="overview", path=str(repo), top=3)
    assert out["ok"], out
    assert out["top"][0]["path"] == "pkg/core.py"
    assert out["unreachable"] == ["pkg/orphan.py"]


async def test_refresh_and_ingest_round_trip(server, tmp_path):
    from gingugu.xray import extract

    repo = _repo(tmp_path)
    made = await _x(server, action="refresh", path=str(repo))
    assert made["ok"] and made["created"] is True
    body = extract.extract(repo).payload()
    again = await _x(server, action="ingest", payload=body, namespace="other")
    assert again["ok"] and again["created"] is True
    by_fp = await _x(
        server,
        action="blast",
        files="pkg/core.py",
        fingerprint=body["fingerprint"],
        namespace="other",
    )
    assert by_fp["code_total"] == 2


async def test_unknown_fingerprint_reports_missing(server):
    out = await _x(server, action="overview", fingerprint="f" * 64)
    assert out == {
        "ok": False,
        "missing_snapshot": True,
        "fingerprint": "f" * 64,
        "error": "no X-ray snapshot of that content; refresh or ingest one first",
    }


async def test_errors_are_structured(server, tmp_path):
    assert (await _x(server, action="nope"))["ok"] is False
    assert (await _x(server, action="blast", path=str(_repo(tmp_path))))["ok"] is False
    assert (await _x(server, action="overview", path=str(tmp_path / "not-a-repo")))["ok"] is False
    assert (await _x(server, action="ingest", payload={"fingerprint": "x"}))["ok"] is False
