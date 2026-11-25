"""Sibling discovery reads checkouts one level inside a group folder.

People group clones: ``~/GIT/platform/<repo>``. Sibling discovery read only each
search root's direct children, so a group folder (a child with no ``.git``) hid
every checkout inside it, ``links`` read 2 checkouts on a machine with dozens, and
``_prune`` dropped every mention of the hidden ones. Now each root is read in two
passes - its direct checkouts first, then one level into each child that is not
a checkout - inside the same ``MAX_SIBLINGS`` cap, so a crowded group folder can
never push a direct checkout out. ``repo_id.find`` looks in the same places.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gingugu.xray import docs_map, repo_id
from tests.test_xray_edit_hook import _git


def _checkout(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    return path


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "GIT"
    r.mkdir()
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(r))
    return r


def _names(paths: list[Path], root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in paths]


def test_a_checkout_inside_a_group_folder_is_a_sibling(root):
    me = _checkout(root / "me")
    _checkout(root / "platform" / "billing")
    _checkout(root / "platform" / "ledger")
    assert _names(docs_map._candidates(me), root) == [
        "platform/billing",
        "platform/ledger",
    ]


def test_direct_checkouts_come_before_grouped_ones(root):
    me = _checkout(root / "me")
    _checkout(root / "aaa-group" / "inner")
    _checkout(root / "zzz")
    assert _names(docs_map._candidates(me), root) == ["zzz", "aaa-group/inner"]


def test_only_one_level_is_descended(root):
    me = _checkout(root / "me")
    _checkout(root / "group" / "sub" / "deep")
    assert docs_map._candidates(me) == []


def test_a_checkout_is_never_descended_into(root):
    me = _checkout(root / "me")
    _checkout(root / "outer")
    _checkout(root / "outer" / "vendored")  # a nested clone inside a checkout
    assert _names(docs_map._candidates(me), root) == ["outer"]


def test_this_checkout_inside_a_group_is_still_skipped(root):
    me = _checkout(root / "group" / "me")
    _checkout(root / "group" / "other")
    assert _names(docs_map._candidates(me), root) == ["group/other"]


def test_the_cap_holds_across_both_passes_and_direct_ones_win(root, monkeypatch):
    monkeypatch.setattr(docs_map, "MAX_SIBLINGS", 3)
    me = _checkout(root / "me")
    for n in ("a", "b"):
        _checkout(root / n)
    for n in ("x", "y", "z"):
        _checkout(root / "group" / n)
    assert _names(docs_map._candidates(me), root) == ["a", "b", "group/x"]


def test_hidden_and_unreadable_entries_are_skipped(root):
    me = _checkout(root / "me")
    (root / "loose.txt").write_text("x")
    (root / "group").mkdir()
    (root / "group" / "file.txt").write_text("x")
    _checkout(root / ".cache" / "inner")  # a dot folder is never a group
    assert docs_map._candidates(me) == []


def test_a_symlinked_group_folder_is_never_descended(root, tmp_path):
    me = _checkout(root / "me")
    _checkout(tmp_path / "elsewhere" / "theirs")
    (root / "linked").symlink_to(tmp_path / "elsewhere")
    assert docs_map._candidates(me) == []
    assert repo_id.find("theirs", None) is None


def test_a_crowded_folder_is_read_only_so_deep(root, monkeypatch):
    monkeypatch.setattr(repo_id, "MAX_SCAN", 5)
    me = _checkout(root / "me")
    group = root / "group"
    group.mkdir()
    for i in range(50):
        (group / f"junk{i:02}").mkdir()
    assert len(repo_id.entries(group)) == 5  # never reads a crowded folder to the end
    assert len(repo_id.entries(root)) == 2  # me, group
    assert docs_map._candidates(me) == []


def test_find_reaches_a_checkout_inside_a_group_folder(root):
    billing = _checkout(root / "platform" / "billing")
    assert repo_id.find("billing", None) == billing


def test_find_prefers_a_direct_checkout_of_the_same_name(root):
    direct = _checkout(root / "billing")
    _checkout(root / "platform" / "billing")
    assert repo_id.find("billing", None) == direct


def test_find_checks_identity_inside_a_group_folder(root):
    billing = _checkout(root / "platform" / "billing")
    _git(billing, "remote", "add", "origin", "git@github.com:acme/billing.git")
    assert repo_id.find("acme/billing", None) == billing
    assert repo_id.find("other/billing", None) is None


def test_find_never_descends_past_one_level(root):
    _checkout(root / "a" / "b" / "billing")
    assert repo_id.find("billing", None) is None
