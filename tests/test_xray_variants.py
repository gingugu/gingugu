"""``code_xray`` action ``variants``: how far this repo's flavours have drifted apart.

Some repos are one thing in several flavours - ``app-us``,
``app-gov``, ``app-eu`` - copied, then edited apart. The
drift between them is the risk: a fix landed in one flavour and not the others.
``variants`` finds the sibling checkouts that are flavours of this one (same name
once the last ``-segment`` is dropped, or one named exactly that stem) and, for
each, compares the files both track, path by path:

- ``drift``: changed lines over lines, across the shared files - a number to
  track from week to week. A file's changed lines are the lines of the longer
  side that ``difflib`` does not match; its lines are the longer side's count.
  A binary file (a NUL byte) counts as one line, changed or not.
- the files that differ most, and the files only one side tracks.

Computed on this machine from both checkouts; no file content leaves it, only
names and counts. Under ``gingugu serve`` the brain refuses it and the proxy
answers locally, like ``links``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gingugu.xray import variants, variants_tool
from tests.proxy_fixtures import make_brain, payload, serving, through_proxy
from tests.test_xray_edit_hook import _git

# --- pure: names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, want",
    [
        ("app-us", "app"),
        ("shared-app-gov", "shared-app"),
        ("App-EU", "app"),
        ("solo", "solo"),
        ("trailing-", "trailing-"),  # an empty last segment is no flavour
    ],
)
def test_stem_drops_the_last_segment(name, want):
    assert variants.stem(name) == want


def test_flavours_share_a_stem_or_one_is_the_stem():
    me = "app-us"
    names = ["app-gov", "app", "app-tools-x", "other-gov", "ap"]
    assert variants.flavours(me, names) == ["app", "app-gov"]
    assert variants.flavours("app", ["app-gov", "ab-x"]) == ["app-gov"]
    assert variants.flavours("solo", ["solo-x", "other"]) == ["solo-x"]
    assert variants.flavours(me, [me]) == []  # never itself


# --- pure: drift ------------------------------------------------------------------------


def test_identical_trees_have_zero_drift():
    a = {"x.tf": b"a\nb\n", "y.yml": b"c\n"}
    d = variants.drift(a, dict(a))
    assert (d["shared"], d["identical"], d["differing"], d["drift"]) == (2, 2, 0, 0.0)
    assert d["files"] == [] and d["only_here"] == [] and d["only_there"] == []


def test_drift_is_changed_lines_over_lines_across_shared_files():
    mine = {"a.tf": b"1\n2\n3\n4\n", "b.tf": b"x\ny\n", "only-mine.tf": b"z\n"}
    theirs = {"a.tf": b"1\n2\nTHREE\n4\n5\n6\n", "b.tf": b"x\ny\n", "only-theirs.yml": b"q\n"}
    d = variants.drift(mine, theirs)
    # a.tf: longer side 6 lines, 3 matched -> 3 changed; b.tf: 2 lines, 0 changed.
    assert (d["changed_lines"], d["total_lines"]) == (3, 8)
    assert d["drift"] == pytest.approx(3 / 8)
    assert d["files"] == [{"path": "a.tf", "changed": 3, "lines": 6}]
    assert (d["identical"], d["differing"]) == (1, 1)
    assert d["only_here"] == ["only-mine.tf"] and d["only_there"] == ["only-theirs.yml"]


def test_files_are_ordered_by_changed_lines_then_path():
    mine = {"b.tf": b"1\n", "a.tf": b"1\n", "c.tf": b"1\n2\n3\n"}
    theirs = {"b.tf": b"2\n", "a.tf": b"2\n", "c.tf": b"x\ny\nz\n"}
    assert [f["path"] for f in variants.drift(mine, theirs)["files"]] == ["c.tf", "a.tf", "b.tf"]


def test_a_binary_file_counts_as_one_line():
    d = variants.drift({"i.png": b"\x89PNG\0a"}, {"i.png": b"\x89PNG\0b"})
    assert d["files"] == [{"path": "i.png", "changed": 1, "lines": 1}]
    assert d["drift"] == 1.0
    same = variants.drift({"i.png": b"\0"}, {"i.png": b"\0"})
    assert (same["total_lines"], same["drift"]) == (1, 0.0)


def test_nothing_shared_is_zero_drift_not_a_division_error():
    d = variants.drift({"a": b"1\n"}, {"b": b"1\n"})
    assert (d["shared"], d["drift"], d["total_lines"]) == (0, 0.0, 0)


def test_an_empty_file_on_both_sides_is_identical_and_weightless():
    d = variants.drift({"e": b""}, {"e": b""})
    assert (d["identical"], d["total_lines"], d["drift"]) == (1, 0, 0.0)


def test_a_huge_repetitive_file_never_stalls_the_diff():
    a = b"}\n" * 40_000 + b"a\n"
    b = b"x\n" + b"}\n" * 40_000
    start = time.monotonic()
    d = variants.drift({"big.tf": a}, {"big.tf": b})
    assert time.monotonic() - start < 2.0
    assert 0 < d["changed_lines"] <= d["total_lines"] == 40_001


def test_common_ends_are_matched_before_the_diff():
    a = b"".join(b"l%d\n" % i for i in range(10_000))
    b = a.replace(b"l5000\n", b"CHANGED\n")
    d = variants.drift({"f": a}, {"f": b})
    assert (d["changed_lines"], d["total_lines"]) == (1, 10_000)


# --- the tool ---------------------------------------------------------------------------


def _checkout(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    for rel, text in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


BASE = {"main.tf": "a\nb\nc\nd\n", "ci/deploy.yml": "x\ny\n", "README.md": "r\n"}


@pytest.fixture
def family(tmp_path, monkeypatch):
    root = tmp_path / "GIT"
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(root))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    me = _checkout(root / "app-us", BASE)
    _checkout(root / "app-gov", {**BASE, "main.tf": "a\nb\nGOV\nd\n", "gov.tf": "g\n"})
    _checkout(root / "platform" / "app-eu", dict(BASE))  # grouped: still found
    _checkout(root / "unrelated", BASE)
    (me / "untracked.tf").write_text("never compared\n")
    return me


def test_answer_compares_each_flavour(family):
    out = variants_tool.answer(str(family))
    assert out["ok"] is True
    rows = {v["name"]: v for v in out["variants"]}
    assert set(rows) == {"app-gov", "app-eu"}
    gov, fed = rows["app-gov"], rows["app-eu"]
    assert (gov["shared"], gov["differing"], gov["only_there"]) == (3, 1, ["gov.tf"])
    assert gov["files"] == [{"path": "main.tf", "changed": 1, "lines": 4}]
    assert gov["drift"] == pytest.approx(1 / 7, abs=1e-3)
    assert (fed["drift"], fed["differing"], fed["only_here"]) == (0.0, 0, [])
    assert out["variants"][0]["name"] == "app-gov"  # most drift first
    assert out["text"].startswith("GINGUGU X-RAY VARIANTS:")
    assert "app-gov" in out["text"] and "main.tf" in out["text"]
    assert "caveat" in out


def test_files_focuses_the_comparison(family):
    out = variants_tool.answer(str(family), files="ci/deploy.yml")
    gov = next(v for v in out["variants"] if v["name"] == "app-gov")
    assert (gov["shared"], gov["differing"], gov["only_there"]) == (1, 0, [])


def test_no_flavours_is_an_ok_empty_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "GIT"))
    me = _checkout(tmp_path / "GIT" / "solo", {"a.tf": "1\n"})
    out = variants_tool.answer(str(me))
    assert out["ok"] is True and out["variants"] == []
    assert "no sibling checkout" in out["text"]


def test_limit_caps_the_lists_not_the_counts(family):
    out = variants_tool.answer(str(family), limit=0)
    gov = next(v for v in out["variants"] if v["name"] == "app-gov")
    assert gov["files"] == [] and gov["only_there"] == [] and gov["differing"] == 1
    assert gov["only_there_total"] == 1 and gov["only_here_total"] == 0


def test_a_symlink_in_a_flavour_is_never_followed(family, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("s\n")
    gov = family.parent / "app-gov"
    (gov / "README.md").unlink()
    (gov / "README.md").symlink_to(secret)
    _git(gov, "add", "-A")
    _git(gov, "commit", "-qm", "link")
    out = variants_tool.answer(str(family))
    row = next(v for v in out["variants"] if v["name"] == "app-gov")
    assert all(f["path"] != "README.md" for f in row["files"])
    assert row["shared"] == 2  # the link is left out, not read


# --- wiring -----------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_NAMESPACE", "repo")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def test_variants_on_stdio(family, server):
    res = await server.call_tool("code_xray", {"action": "variants", "path": str(family)})
    out = json.loads(res.content[0].text)
    assert out["ok"] is True, out
    assert {v["name"] for v in out["variants"]} == {"app-gov", "app-eu"}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def test_the_brain_refuses_variants(brain):
    from tests.test_board_twins_http import _call, _derive

    async with serving(brain.app()) as served:
        token = await _derive(served, brain.machine)
        out = await _call(served, token, "code_xray", action="variants")
    assert out["ok"] is False and "gingugu serve" in out["error"]


async def test_the_proxy_answers_variants_locally(brain, family, monkeypatch):
    from gingugu import proxy_xray

    async def boom(self, args):
        raise AssertionError(f"brain was called: {args}")

    monkeypatch.setattr(proxy_xray.XrayRelay, "_call", boom)
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="proj") as (session, _):
            await session.initialize()
            out = payload(
                await session.call_tool("code_xray", {"action": "variants", "path": str(family)})
            )
    assert out["ok"], out


def test_cli_variants(family, capsys):
    from gingugu import xray_cli

    assert xray_cli.main(["variants", "--path", str(family)]) == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY VARIANTS:")
    assert xray_cli.main(["variants", "--path", str(family), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["variants"]) == 2


def test_past_the_deadline_a_diff_is_an_upper_bound():
    a, b = b"1\n2\nx\n3\n", b"1\n2\ny\nz\n3\n"
    assert variants.file_drift(a, b) == (2, 5)
    assert variants.file_drift(a, b, exact=False) == (2, 5)  # the middle only: still cheap
    c, d = b"a\nb\nc\nd\n", b"a\nc\nb\nd\n"
    assert variants.file_drift(c, d) == (1, 4)
    assert variants.file_drift(c, d, exact=False) == (2, 4)


def test_a_fifo_in_a_flavour_is_never_opened(family):
    import os
    import sys

    if not hasattr(os, "mkfifo") or sys.platform == "win32":
        pytest.skip("no FIFOs here")
    gov = family.parent / "app-gov"
    (gov / "README.md").unlink()
    os.mkfifo(gov / "README.md")  # git still tracks the path; the read must not block
    out = variants_tool.answer(str(family))
    row = next(v for v in out["variants"] if v["name"] == "app-gov")
    assert row["shared"] == 2
