"""A fake language adapter for the X-ray framework tests.

``.fk`` files hold lines ``import <path>`` (a file edge) and ``pkg <dir>`` (an
import of the package in ``<dir>``); ``fake.mod`` is its manifest. Package
nodes are named ``fake:<dir>`` and reach their files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

import pytest

from gingugu.xray import lang
from gingugu.xray.model import Edge, FileInfo, LangResult


class FakeAdapter:
    name = "fake"
    extensions = (".fk",)
    manifests = ("fake.mod",)
    seen_manifests: dict[str, bytes] = {}

    def parse(self, rel, data):
        return data.decode()

    def extract(self, sources, manifests):
        return self.link({rel: self.parse(rel, data) for rel, data in sources.items()}, manifests)

    def link(self, sources, manifests):
        FakeAdapter.seen_manifests = dict(manifests)
        out = LangResult()
        dirs: dict[str, list[str]] = {}
        for rel in sources:
            is_test = rel.endswith("_test.fk")
            out.files.append(FileInfo(rel, is_test, rel.endswith("main.fk"), "fake"))
            if not is_test:
                dirs.setdefault(str(PurePosixPath(rel).parent), []).append(rel)
        for d, members in dirs.items():
            node = f"fake:{d}"
            out.files.append(FileInfo(node, False, False, "fake", "package"))
            out.edges += [Edge(node, m, "member", True) for m in members]
        for rel, text in sources.items():
            for line in text.splitlines():
                word, _, arg = line.partition(" ")
                if word == "import":
                    out.edges.append(Edge(rel, arg, "module", True))
                elif word == "pkg":
                    out.edges.append(Edge(rel, f"fake:{arg}", "package", True))
        return out


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(lang, "ADAPTERS", (*lang.ADAPTERS, FakeAdapter()))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())  # write_text turns \n into \r\n on Windows
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# lib/ is one package of two files; app.fk imports it; lib_test.fk tests it.
MIXED = {
    "lib/a.fk": "",
    "lib/b.fk": "",
    "lib/lib_test.fk": "pkg lib\n",
    "app/main.fk": "pkg lib\n",
    "tool.py": "import helper\n",
    "helper.py": "",
    "fake.mod": "root\n",
    "sub/fake.mod": "nested\n",
    "notfake.mod": "ignored\n",
}


# --- helpers for driving a real adapter on bytes, no git ------------------------------


def run_adapter(adapter, sources: dict[str, str], manifests: dict[str, str] | None = None):
    return adapter.extract(
        {p: t.encode() for p, t in sources.items()},
        {p: t.encode() for p, t in (manifests or {}).items()},
    )


def edges(res) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in res.edges}


def edge(res, src: str, dst: str):
    found = [e for e in res.edges if e.src == src and e.dst == dst]
    assert len(found) == 1, f"{src}->{dst}: {found}"
    return found[0]


def node(res, path: str):
    found = [f for f in res.files if f.path == path]
    assert len(found) == 1, f"{path}: {found}"
    return found[0]
