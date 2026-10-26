"""``code_xray`` through the proxy: the checkout is read here, the graph lives there.

The brain under `gingugu serve` refuses any path - it would be the host's disk.
So a call that succeeds with a ``path`` through the proxy proves the proxy did
the reading and sent the brain only a fingerprint and, when needed, a payload.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.proxy_fixtures import make_brain, payload, serving, through_proxy


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "checkout"
    files = {
        "pkg/__init__.py": "",
        "pkg/core.py": "",
        "pkg/svc.py": "from pkg import core\n",
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


async def test_the_brain_refuses_a_path_on_its_own(brain, tmp_path):
    from tests.test_board_twins_http import _call, _derive

    repo = _repo(tmp_path)
    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="overview", path=str(repo))
    assert out["ok"] is False
    assert "gingugu serve" in out["error"]


async def test_blast_through_the_proxy_reads_the_checkout_locally(brain, tmp_path):
    repo = _repo(tmp_path)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            first = payload(
                await session.call_tool(
                    "code_xray",
                    {"action": "blast", "files": str(repo / "pkg/core.py"), "path": str(repo)},
                )
            )
            again = payload(
                await session.call_tool(
                    "code_xray", {"action": "overview", "path": str(repo / "pkg")}
                )
            )
            other = payload(await session.call_tool("memory_stats", {}))
    assert first["ok"], first
    assert first["freshness"] == "current"
    assert [d["path"] for d in first["code"]] == ["pkg/svc.py"]
    assert [d["path"] for d in first["tests"]] == ["tests/test_svc.py"]
    assert again["snapshot"]["snapshot_id"] == first["snapshot"]["snapshot_id"]
    assert other["ok"]  # everything else still relays


async def test_refresh_through_the_proxy(brain, tmp_path):
    repo = _repo(tmp_path)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            made = payload(
                await session.call_tool("code_xray", {"action": "refresh", "path": str(repo)})
            )
            (repo / "pkg/core.py").write_text("x = 1\n")
            edited = payload(
                await session.call_tool("code_xray", {"action": "overview", "path": str(repo)})
            )
    assert made["ok"] and made["created"] is True
    assert edited["snapshot"]["fingerprint"] != made["fingerprint"]


async def test_a_local_failure_is_a_structured_error(brain, tmp_path):
    (tmp_path / "plain").mkdir()
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool(
                    "code_xray", {"action": "overview", "path": str(tmp_path / "plain")}
                )
            )
    assert out["ok"] is False
    assert "git" in out["error"]


async def test_the_proxy_marks_its_own_check_current_and_keeps_paths_home(brain, tmp_path):
    repo = _repo(tmp_path)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "overview", "path": str(repo)})
            )
            outside = payload(
                await session.call_tool(
                    "code_xray",
                    {"action": "blast", "files": str(tmp_path / "x.py"), "path": str(repo)},
                )
            )
    assert out["freshness"] == "current"
    assert outside["ok"] is False and "outside" in outside["error"]
