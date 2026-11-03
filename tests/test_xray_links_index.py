"""X23: reading this checkout and its siblings into facts (``links.index``).

Real git checkouts in tmp, real extractors unless a test needs to count or
break them. Siblings are found under ``MEMORY_XRAY_REPOS``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gingugu.xray.links import index, model
from tests.test_xray_edit_hook import _git


def _checkout(base: Path, name: str, files: dict[str, str], origin: str | None = None) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    if origin:
        _git(repo, "remote", "add", "origin", origin)
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


A = {
    "svc/api.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.post("/internal/resolve")\ndef r(): ...\n'
    ),
    "k8s/ingress.yaml": "kind: Ingress\nspec:\n  rules:\n    - host: svc-a.acme.io\n",
    "node_modules/x/index.js": "app.get('/vendored/route', h)\n",
}
B = {
    "src/client.ts": 'const r = await fetch("https://svc-a.acme.io/internal/resolve")\n',
    ".github/workflows/t.yml": "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n"
    "        with:\n          repository: acme/svc-a\n",
}


@pytest.fixture
def eco(tmp_path, monkeypatch):
    base = tmp_path / "GIT"
    a = _checkout(base, "svc-a", A, "git@github.com:acme/svc-a.git")
    _checkout(base, "svc-b", B, "https://github.com/acme/svc-b.git")
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(base))
    state = tmp_path / "state"
    state.mkdir()
    return a, state


def test_reads_this_checkout_and_its_siblings(eco):
    a, state = eco
    e = index.read(a, state, budget_s=10)
    assert e.me == "acme/svc-a" and set(e.repos) == {"acme/svc-a", "acme/svc-b"}
    assert e.roots["acme/svc-b"].name == "svc-b" and e.partial == {} and e.unscanned == ()
    provided = {(f.kind, f.key) for f in e.repos["acme/svc-a"].provided}
    assert (model.ROUTE, "/internal/resolve") in provided and (
        model.HOST,
        "svc-a.acme.io",
    ) in provided
    assert not any(f.path.startswith("node_modules/") for f in e.repos["acme/svc-a"].provided)
    mentioned = {(f.kind, f.key) for f in e.repos["acme/svc-b"].mentioned}
    assert {(model.CI, "acme/svc-a"), (model.HOST, "svc-a.acme.io")} <= mentioned
    assert "src/client.ts" in e.repos["acme/svc-b"].files


def test_a_second_read_of_unchanged_repos_extracts_nothing(eco):
    a, state = eco
    calls = []

    def counting(path, text):
        calls.append(path)
        return index._default_extract(path, text)

    first = index.read(a, state, budget_s=10, extract=counting)
    n = len(calls)
    assert n > 0
    second = index.read(a, state, budget_s=10, extract=counting)
    assert len(calls) == n and second.repos == first.repos


def test_a_changed_file_is_read_again(eco):
    a, state = eco
    index.read(a, state, budget_s=10)
    (a / "svc/api.py").write_text('@app.get("/internal/other")\n')
    e = index.read(a, state, budget_s=10)
    routes = {f.key for f in e.repos["acme/svc-a"].provided if f.kind == model.ROUTE}
    assert routes == {"/internal/other"}


def test_untracked_files_count_here_but_not_in_siblings(eco, tmp_path):
    a, state = eco
    (a / "svc/new.py").write_text('@app.get("/brand/new")\n')
    (tmp_path / "GIT/svc-b/src/wip.ts").write_text('fetch("https://wip.acme.io/x/y")\n')
    e = index.read(a, state, budget_s=10)
    assert any(f.key == "/brand/new" for f in e.repos["acme/svc-a"].provided)
    assert not any(f.path == "src/wip.ts" for f in e.repos["acme/svc-b"].mentioned)


def test_past_the_budget_a_repo_keeps_its_cache_and_counts_what_it_did_not_read(eco):
    a, state = eco
    index.read(a, state, budget_s=10)
    (a / "svc/api.py").write_text('@app.get("/changed/route")\n')
    e = index.read(a, state, budget_s=0)
    assert e.partial.get("acme/svc-a") == 1
    routes = {f.key for f in e.repos["acme/svc-a"].provided if f.kind == model.ROUTE}
    assert routes == {"/internal/resolve"}  # the stale entry, flagged by the count


def test_a_sibling_with_nothing_read_or_cached_is_unscanned(eco):
    a, state = eco
    e = index.read(a, state, budget_s=0)
    assert "svc-b" in e.unscanned and "acme/svc-b" not in e.repos


def test_a_second_checkout_of_this_repo_is_not_a_sibling(eco, tmp_path):
    a, state = eco
    _checkout(tmp_path / "GIT", "svc-a-copy", A, "git@github.com:acme/svc-a.git")
    e = index.read(a, state, budget_s=10)
    assert set(e.repos) == {"acme/svc-a", "acme/svc-b"}


def test_an_extractor_that_raises_costs_that_file_only(eco):
    a, state = eco

    def flaky(path, text):
        if path == "svc/api.py":
            raise RuntimeError("boom")
        return index._default_extract(path, text)

    e = index.read(a, state, budget_s=10, extract=flaky)
    assert any(f.key == "svc-a.acme.io" for f in e.repos["acme/svc-a"].provided)


def test_a_symlink_out_of_the_checkout_is_never_read(eco, tmp_path):
    a, state = eco
    secret = tmp_path / "secret.yaml"
    secret.write_text("kind: Ingress\nspec:\n  rules:\n    - host: leaked.acme.io\n")
    os.symlink(secret, a / "k8s/link.yaml")
    _git(a, "add", "-A")
    _git(a, "commit", "-q", "-m", "link")
    e = index.read(a, state, budget_s=10)
    assert not any(f.key == "leaked.acme.io" for f in e.repos["acme/svc-a"].provided)


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"v": index.EXTRACT_VERSION, "files": {"svc/api.py": [1, 2, [["x"]], []]}}),
        json.dumps({"v": index.EXTRACT_VERSION, "files": {"svc/api.py": ["1", 2, [], []]}}),
        json.dumps({"v": index.EXTRACT_VERSION, "files": []}),
        json.dumps({"v": -1, "files": {}}),
    ],
)
def test_a_malformed_cache_costs_a_rebuild_and_no_more(eco, bad):
    a, state = eco
    index._cache_path(a, state).write_text(bad)
    e = index.read(a, state, budget_s=10)
    assert any(f.key == "/internal/resolve" for f in e.repos["acme/svc-a"].provided)


def test_a_poisoned_cache_entry_is_dropped_not_trusted(eco):
    a, state = eco
    index.read(a, state, budget_s=10)
    path = index._cache_path(a, state)
    raw = json.loads(path.read_text())
    st = (a / "svc/api.py").stat()
    raw["files"]["svc/api.py"] = [st.st_size, st.st_mtime_ns, [["route", "/x/y", "1", "", ""]], []]
    path.write_text(json.dumps(raw))
    e = index.read(a, state, budget_s=10)
    keys = {f.key for f in e.repos["acme/svc-a"].provided}
    assert "/x/y" not in keys and "/internal/resolve" in keys


def test_names_are_capped_per_file(eco):
    a, state = eco
    many = "\n".join(f'X{i} = "resource-name-{i}"' for i in range(1000))
    (a / "svc/names.py").write_text(many)
    e = index.read(a, state, budget_s=10)
    names = [f for f in e.repos["acme/svc-a"].mentioned if f.path == "svc/names.py"]
    assert len([f for f in names if f.kind == model.NAME]) == index.MAX_NAMES_PER_FILE
