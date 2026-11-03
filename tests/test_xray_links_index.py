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
    # past the budget not even a stat runs: every wanted file counts as unchecked
    assert e.partial.get("acme/svc-a") == 2
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


def test_a_linked_directory_out_of_the_checkout_is_never_read(eco, tmp_path):
    a, state = eco
    outside = tmp_path / "outside"
    (outside / "k8s").mkdir(parents=True)
    (outside / "k8s/i.yaml").write_text(
        "kind: Ingress\nspec:\n  rules:\n    - host: leak.acme.io\n"
    )
    os.symlink(outside / "k8s", a / "deploy")
    # a crafted index can list a name under the link; the reader must refuse it
    found = index._Inside(a)("deploy/i.yaml")
    assert found is None
    assert index._Inside(a)("svc/api.py") is not None


def test_a_crafted_deep_name_is_refused_and_the_dir_cache_stays_bounded(eco):
    a, _state = eco
    inside = index._Inside(a)
    assert inside("/".join(["a"] * 100) + "/f.json") is None
    for i in range(index._Inside.MAX_DIRS + 50):
        inside(f"d{i}/x/f.json")
    assert len(inside.dirs) <= index._Inside.MAX_DIRS + 3


def test_a_hung_sibling_is_left_behind_at_the_deadline_plus_grace(monkeypatch):
    import threading
    import time as _t

    from gingugu.xray.links import walk

    monkeypatch.setattr(walk, "GRACE_S", 0.2)
    stop = threading.Event()

    def work(x):
        if x == "hung":
            stop.wait(30)
        return x

    start = _t.monotonic()
    out = walk.gather(["a", "hung", "b"], work, _t.monotonic() + 0.1)
    stop.set()
    assert _t.monotonic() - start < 2
    assert out[0] == "a" and out[2] == "b" and out[1] is walk.LATE


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


@pytest.mark.parametrize(
    "row",
    [
        ["not-a-kind", "/x/y", 1, "", ""],
        ["route", "/" + "a" * 5000, 1, "", ""],
        ["route", "/x/y", 0, "", ""],
        ["cite", "acme/b", 1, "--output=/tmp/pwned", "a.py"],
    ],
)
def test_a_cache_row_out_of_shape_drops_its_file(eco, row):
    a, state = eco
    index.read(a, state, budget_s=10)
    path = index._cache_path(a, state)
    raw = json.loads(path.read_text())
    st = (a / "svc/api.py").stat()
    raw["files"]["svc/api.py"] = [st.st_size, st.st_mtime_ns, [row], []]
    path.write_text(json.dumps(raw))
    _ident, entries = index._load(a, state)
    if row[0] == "cite":
        # a hostile detail is a string like any other here: it is the drift check's
        # job to refuse it, see test_xray_links_tool's hostile sha test
        assert "svc/api.py" in entries
    else:
        assert "svc/api.py" not in entries


def test_past_the_budget_a_cached_sibling_is_served_without_asking_git(eco, monkeypatch):
    a, state = eco
    index.read(a, state, budget_s=10)
    from gingugu.xray import scan

    asked = []
    real = scan.git

    def spy(root, *args, **kw):
        asked.append(Path(root).name)
        return real(root, *args, **kw)

    monkeypatch.setattr(scan, "git", spy)
    e = index.read(a, state, budget_s=0)
    assert "svc-b" not in asked
    assert "acme/svc-b" in e.repos and e.partial["acme/svc-b"] > 0
    assert any(f.key == "svc-a.acme.io" for f in e.repos["acme/svc-b"].mentioned)


def test_loose_names_are_capped_per_repo():
    facts = [
        model.Fact(model.RESOURCE, f"*-q{i:04d}-name", f"f{i}.tf", 1)
        for i in range(index.MAX_LOOSE_PER_REPO + 50)
    ] + [model.Fact(model.HOST, "a.acme.io", "z.yaml", 1)]
    kept = index._bounded(facts)
    assert len([f for f in kept if f.kind == model.RESOURCE]) == index.MAX_LOOSE_PER_REPO
    assert kept[-1].kind == model.HOST


def test_the_file_reader_refuses_a_link_and_a_fifo(tmp_path):
    from gingugu.xray import docs_map

    real = tmp_path / "real.md"
    real.write_text("x")
    (tmp_path / "link.md").symlink_to(real)
    with pytest.raises(OSError):
        docs_map._read_text(tmp_path / "link.md")
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe.md")
        with pytest.raises(OSError):
            docs_map._read_text(tmp_path / "pipe.md")  # never blocks waiting for a writer


def test_repo_names_are_pruned_to_real_checkouts_before_any_cap(eco, monkeypatch):
    a, state = eco
    # thousands of hyphenated words first, the real sibling name last: the cap must
    # count only names of checkouts read here, or the real one is crowded out
    noise = "\n".join(f"# word-{i} other-{i}" for i in range(3000))
    (a / "docs").mkdir()
    for k in range(10):
        (a / f"docs/a{k}.md").write_text(noise)
    (a / "docs/z.md").write_text("Calls svc-b for everything.\n")
    monkeypatch.setattr(index, "MAX_NAMES_PER_REPO", 50)
    e = index.read(a, state, budget_s=30)
    named = {(f.key, f.path) for f in e.repos["acme/svc-a"].mentioned if f.kind == model.REPO_NAME}
    assert ("svc-b", "docs/z.md") in named
    assert not any(k.startswith("word-") for k, _ in named)


def test_names_are_capped_per_file(eco):
    a, state = eco
    many = "\n".join(f'X{i} = "resource-name-{i}"' for i in range(1000))
    (a / "svc/names.py").write_text(many)
    e = index.read(a, state, budget_s=10)
    names = [f for f in e.repos["acme/svc-a"].mentioned if f.path == "svc/names.py"]
    assert len([f for f in names if f.kind == model.NAME]) == index.MAX_NAMES_PER_FILE
