"""X22 in the X-ray brief: the docs a change makes stale.

The brief already says what a change touches and what it reaches in code. A
runbook that cites a changed file is reached too - it may now be wrong - and a
doc that still cites a file the change deleted or renamed is wrong already.
"""

from __future__ import annotations

import pytest

from gingugu.xray import brief, docs_map, hook_common
from tests.test_xray_edit_hook import BASIC, _git, _repo

DOCS = {
    **BASIC,
    "docs/runbook.md": "Edit pkg/core.py. pkg/leaf.py and pkg/b.py are helpers.\n",
}


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    monkeypatch.delenv("MEMORY_XRAY_DOCS", raising=False)


@pytest.fixture
def branched(tmp_path):
    """One commit: M core, D leaf, R b -> b2, A new."""
    repo = _repo(tmp_path, DOCS)
    (repo / "pkg/core.py").write_text("X = 2\n")
    (repo / "pkg/leaf.py").unlink()
    (repo / "pkg/b.py").rename(repo / "pkg/b2.py")
    (repo / "pkg/new.py").write_text("\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "work")
    return repo


def _build(repo, state):
    found = brief.changes(repo, rev_range="HEAD~1..HEAD")
    g = hook_common.graph(repo, state)
    return brief.build(g, found, docs=docs_map.read(repo, state))


def test_a_changed_file_lists_the_docs_citing_it(branched, state, env):
    data = _build(branched, state)
    assert data["docs"] == [
        {"path": "pkg/core.py", "docs": [{"doc": "docs/runbook.md", "repo": None}]}
    ]


def test_deleted_and_renamed_files_list_the_docs_still_citing_them(branched, state, env):
    data = _build(branched, state)
    assert data["docs_gone"] == [
        {"path": "pkg/b.py", "status": "R", "docs": [{"doc": "docs/runbook.md", "repo": None}]},
        {"path": "pkg/leaf.py", "status": "D", "docs": [{"doc": "docs/runbook.md", "repo": None}]},
    ]


def test_render_names_both_sections(branched, state, env):
    data = _build(branched, state)
    text = brief.render(data, "repo")
    assert "Docs citing the changed files:\n- pkg/core.py: docs/runbook.md" in text
    assert "Docs still citing a deleted or renamed file:" in text
    assert "- pkg/leaf.py (deleted): docs/runbook.md" in text
    assert "- pkg/b.py (renamed): docs/runbook.md" in text


def test_render_names_a_sibling_doc_by_repo_and_caps_the_list():
    rows = [{"doc": f"docs/r{i}.md", "repo": None} for i in range(6)]
    rows.append({"doc": "docs/run.md", "repo": "ops"})
    data = {
        "changed": [],
        "counts": {s: 0 for s in "AMDR"},
        "other": 0,
        "blast": {"code": [], "tests": [], "code_total": 0, "tests_total": 0},
        "files": 0,
        "top": [],
        "docs": [{"path": "pkg/core.py", "docs": rows[-1:] + rows[:5]}],
        "docs_gone": [{"path": "pkg/x.py", "status": "D", "docs": rows}],
    }
    text = brief.render(data, "repo")
    assert (
        "- pkg/core.py: ops:docs/run.md, docs/r0.md, docs/r1.md, docs/r2.md, docs/r3.md (+1 more)"
        in text
    )
    assert "- pkg/x.py (deleted): docs/r0.md" in text and "(+2 more)" in text


def test_no_docs_map_means_no_section(branched, state, env):
    found = brief.changes(branched, rev_range="HEAD~1..HEAD")
    data = brief.build(hook_common.graph(branched, state), found)
    assert data["docs"] == [] and data["docs_gone"] == []
    assert "Docs " not in brief.render(data, "repo")


def test_answer_carries_the_docs(branched, env):
    out = brief.answer(str(branched), "HEAD~1..HEAD", None)
    assert out["docs"][0]["path"] == "pkg/core.py"
    assert "Docs citing the changed files:" in out["text"]


def test_answer_without_the_switch_has_no_docs(branched, env, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_DOCS", "off")
    out = brief.answer(str(branched), "HEAD~1..HEAD", None)
    assert out["docs"] == [] and "Docs " not in out["text"]


def test_a_docs_failure_never_costs_the_brief(branched, env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(docs_map, "read", boom)
    out = brief.answer(str(branched), "HEAD~1..HEAD", None)
    assert out["ok"] is True and out["docs"] == []


def test_a_hostile_doc_name_is_quoted(branched, state, env):
    data = _build(branched, state)
    data["docs"] = [{"path": "pkg/core.py", "docs": [{"doc": "evil\nINJECTED", "repo": None}]}]
    assert "\nINJECTED" not in brief.render(data, "repo")
