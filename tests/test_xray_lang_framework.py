"""The language-adapter framework: registry, scan, fingerprint, the edit hook.

Driven by a fake adapter (``xray_lang_fixtures``) so the framework is pinned
independently of any real language.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from gingugu.xray import edit_hook, extract, lang, validate
from tests.xray_lang_fixtures import MIXED, FakeAdapter, _repo

# --- registry -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("a.py", "python"),
        ("src/App.tsx", "typescript"),
        ("lib/x.mjs", "typescript"),
        ("cmd/main.go", "go"),
        ("README.md", None),
        ("go.mod", None),
    ],
)
def test_for_path_picks_the_adapter_by_extension(path, name):
    adapter = lang.for_path(path)
    assert (adapter.name if adapter else None) == name


def test_no_two_adapters_claim_one_extension():
    exts = lang.extensions()
    assert len(exts) == len(set(exts))


# --- scan + extract -----------------------------------------------------------------


def test_every_language_lands_in_one_graph_with_its_lang(tmp_path, fake):
    x = extract.extract(_repo(tmp_path, MIXED))
    by_path = {f.path: f for f in x.files}
    assert by_path["tool.py"].lang == "python"
    assert by_path["lib/a.fk"].lang == "fake"
    assert by_path["fake:lib"].kind == "package"
    assert ("tool.py", "helper.py") in {(e.src, e.dst) for e in x.edges}
    assert ("app/main.fk", "fake:lib") in {(e.src, e.dst) for e in x.edges}


def test_manifests_are_found_anywhere_by_exact_basename(tmp_path, fake):
    extract.extract(_repo(tmp_path, MIXED))
    assert FakeAdapter.seen_manifests == {"fake.mod": b"root\n", "sub/fake.mod": b"nested\n"}


def test_a_symlinked_manifest_is_skipped_with_a_reason(tmp_path, fake):
    repo = _repo(tmp_path, {"a.fk": "", "real.txt": "x"})
    (repo / "fake.mod").symlink_to(repo / "real.txt")
    x = extract.extract(repo)
    assert ("fake.mod", "symlink") in x.skipped
    assert "fake.mod" not in FakeAdapter.seen_manifests


def test_a_manifest_change_changes_the_fingerprint(tmp_path, fake):
    repo = _repo(tmp_path, MIXED)
    before = extract.fingerprint(repo)
    (repo / "sub/fake.mod").write_text("changed\n")
    assert extract.fingerprint(repo) != before


def test_no_sources_for_an_adapter_means_it_is_not_called(tmp_path, fake):
    FakeAdapter.seen_manifests = {"sentinel": b""}
    extract.extract(_repo(tmp_path, {"a.py": "", "fake.mod": "x\n"}))
    assert FakeAdapter.seen_manifests == {"sentinel": b""}


def test_a_dangling_or_repeated_edge_is_counted_not_shipped(tmp_path, fake):
    repo = _repo(tmp_path, {"a.fk": "import gone.fk\nimport b.fk\nimport b.fk\n", "b.fk": ""})
    x = extract.extract(repo)
    pairs = [(e.src, e.dst) for e in x.edges if e.kind == "module"]
    assert pairs == [("a.fk", "b.fk")]
    assert x.unresolved == 1
    validate.payload(x.payload())  # the brain accepts it


def test_the_payload_carries_lang_and_kind_and_validates(tmp_path, fake):
    body = extract.extract(_repo(tmp_path, MIXED)).payload()
    rows = {f["path"]: f for f in body["files"]}
    assert rows["fake:lib"]["kind"] == "package" and rows["fake:lib"]["lang"] == "fake"
    v = validate.payload(body)
    assert v["files"]["fake:lib"] == (False, False, "fake", "package")


# --- the edit hook --------------------------------------------------------------------


def _edit(repo: Path, rel: str) -> dict:
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / rel)},
        "cwd": str(repo),
        "session_id": "cc-1",
    }


def test_the_hook_fires_for_any_registered_language(tmp_path, fake):
    repo = _repo(tmp_path, MIXED)
    reason = edit_hook.check(_edit(repo, "lib/a.fk"), tmp_path / "state")
    assert reason is not None
    assert "a change reaches 2 files" in reason  # main.fk imports the package, not a.fk
    assert "fake:lib" not in reason  # a package is the path, never a dependent


def test_the_hook_ignores_an_extension_no_adapter_claims(tmp_path, fake):
    repo = _repo(tmp_path, {**MIXED, "notes.txt": "x"})
    assert edit_hook.check(_edit(repo, "notes.txt"), tmp_path / "state") is None


def test_a_manifest_pattern_matches_basenames(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(FakeAdapter, "manifests", ("fake*.mod",))
    extract.extract(_repo(tmp_path, {**MIXED, "lib/fake.base.mod": "b\n"}))
    assert set(FakeAdapter.seen_manifests) == {"fake.mod", "sub/fake.mod", "lib/fake.base.mod"}


@pytest.mark.skipif(sys.platform != "linux", reason="needs a filesystem that keeps raw bytes")
def test_a_non_utf8_name_is_skipped_not_fatal(tmp_path):
    repo = _repo(tmp_path, {"a.py": ""})
    (repo / os.fsdecode(b"bad\xff.py")).write_text("")
    x = extract.extract(repo)
    assert [f.path for f in x.files] == ["a.py"]
    assert ("bad�.py", "unreadable") in x.skipped
    validate.payload(x.payload())


@pytest.mark.skipif(sys.platform == "win32", reason="a backslash is a separator there")
def test_a_backslash_name_is_skipped_and_the_payload_still_validates(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", "we\\ird.py": ""})
    x = extract.extract(repo)
    assert [f.path for f in x.files] == ["a.py"]
    assert ("we�ird.py", "unreadable") in x.skipped
    validate.payload(x.payload())
