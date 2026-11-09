"""X22 hook surfaces, hardened: the sibling budget holds inside one sibling, and a
summary whose clock no machine gives reads as bad instead of raising past the hooks.
Fixtures are ``test_xray_docs_hooks``'s."""

from __future__ import annotations

import time

import pytest

from gingugu.xray import docs_map
from tests.test_xray_docs_hooks import _summary, repo, state  # noqa: F401 - fixtures
from tests.test_xray_edit_hook import _git


def test_one_huge_sibling_cannot_outlast_the_budget(repo, state, monkeypatch):  # noqa: F811
    ops = repo.parent / "ops"
    for i in range(20):
        (ops / f"docs/m{i}.md").write_text("repo/pkg/a.py\n")
    _git(ops, "add", "-A")
    _git(ops, "commit", "-q", "-m", "more")
    real = docs_map._read_text

    def slow(path):
        time.sleep(0.1)
        return real(path)

    monkeypatch.setattr(docs_map, "_read_text", slow)
    t = time.monotonic()
    m = docs_map.read(repo, state, budget_s=0.5)
    assert time.monotonic() - t < 1.9  # 21 docs x 0.1s = 2.1s unbudgeted; cut at 0.5s
    assert "ops" not in m.siblings  # unread and never cached: skipped, not half-read


def test_a_sibling_past_the_budget_keeps_its_last_reading(repo, state, monkeypatch):  # noqa: F811
    assert docs_map.read(repo, state).siblings  # cached
    _git(repo.parent / "ops", "commit", "-q", "--allow-empty", "-m", "moved")  # HEAD moves

    def boom(*a, **k):
        raise docs_map.scan.XrayError("past the sibling budget")

    monkeypatch.setattr(docs_map, "_sibling", boom)
    assert "ops" in docs_map.read(repo, state).siblings


@pytest.mark.parametrize("at", [10**400, -1, 2**40])
def test_a_clock_no_machine_gives_says_nothing(repo, state, at):  # noqa: F811
    assert _summary(repo, state, [["docs/a.md", None]], at=at) is None
