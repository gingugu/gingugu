"""X16 incremental refresh: a cached per-file parse gives exactly the cold answer.

The contract under test:

- ``extract.extract(path, parse_cache=<file>)`` returns a payload identical to
  ``extract.extract(path)``, whatever the cache holds - fresh, stale, corrupt,
  from another gingugu version, or unwritable.
- Each adapter exposes ``parse(rel, data)`` (bytes only, JSON-safe result) and
  ``link(raws, manifests)``; only files whose bytes (and extension) the cache
  has not seen are handed to ``parse``.
- The cache file is JSON whose ``raws`` maps adapter name -> {key: raw}, and
  holds only entries for the sources that exist now.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import gingugu
from gingugu.xray import extract, hook_common, lang
from tests.xray_incremental_fixtures import FILES, MUTATIONS, make_repo, write


@pytest.fixture
def parses(monkeypatch) -> list[str]:
    """Every ``rel`` handed to any adapter's ``parse``, in call order."""
    seen: list[str] = []
    for adapter in lang.ADAPTERS:
        orig = adapter.parse

        def spy(rel, data, _orig=orig):
            seen.append(rel)
            return _orig(rel, data)

        monkeypatch.setattr(adapter, "parse", spy)
    return seen


def _cold(repo: Path) -> dict:
    return extract.extract(repo).payload()


def _warm(repo: Path, cache: Path) -> dict:
    return extract.extract(repo, parse_cache=cache).payload()


def test_first_build_with_a_cache_matches_cold_and_parses_every_source(tmp_path, parses):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    assert _warm(repo, cache) == _cold(repo)
    sources = {r for r in FILES if lang.for_path(r) is not None}
    assert set(parses) == sources
    assert cache.is_file()


def test_unchanged_checkout_parses_nothing(tmp_path, parses):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    parses.clear()
    got = _warm(repo, cache)
    assert parses == []
    assert got == _cold(repo)


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_after_a_change_only_new_bytes_are_parsed_and_answer_matches_cold(tmp_path, parses, name):
    mutate, expected = MUTATIONS[name]
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    mutate(repo)
    parses.clear()
    got = _warm(repo, cache)
    assert set(parses) == expected
    assert got == _cold(repo)


def test_a_cache_reused_across_a_sequence_of_changes_stays_exact(tmp_path):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    for name in sorted(MUTATIONS):
        mutate, _ = MUTATIONS[name]
        try:
            mutate(repo)
        except FileNotFoundError:
            continue  # an earlier step already moved this file
        assert _warm(repo, cache) == _cold(repo), name


def test_identical_bytes_under_two_extensions_are_parsed_separately(tmp_path, parses):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    text = FILES["web/src/side.ts"]
    write(repo, "web/src/side.tsx", text)
    write(repo, "web/src/side.js", text)
    parses.clear()
    assert _warm(repo, cache) == _cold(repo)
    assert set(parses) == {"web/src/side.tsx", "web/src/side.js"}


def test_deleted_sources_are_pruned_from_the_cache(tmp_path):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    (repo / "svc/store/db.go").unlink()
    (repo / "app/cli.py").unlink()
    _warm(repo, cache)
    raws = json.loads(cache.read_text())["raws"]
    live = {
        (Path(r).suffix, FILES[r]) for r in FILES if lang.for_path(r) and (repo / r).exists()
    }
    assert sum(len(v) for v in raws.values()) == len(live)


@pytest.mark.parametrize(
    "garbage",
    [b"\x00\xffnot json", b"[]", b'{"raws": 5}', b'{"raws": {"python": []}}', b""],
)
def test_a_corrupt_cache_file_gives_the_cold_answer(tmp_path, garbage):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    cache.write_bytes(garbage)
    assert _warm(repo, cache) == _cold(repo)


@pytest.mark.parametrize("junk", [12345, None, "text", [], {"junk": 1}, [["a", 1, 2]]])
def test_malformed_entries_are_reparsed_not_trusted(tmp_path, parses, junk):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    data = json.loads(cache.read_text())
    for entries in data["raws"].values():
        for key in entries:
            entries[key] = junk
    cache.write_text(json.dumps(data))
    parses.clear()
    got = _warm(repo, cache)
    assert set(parses) == {r for r in FILES if lang.for_path(r) is not None}
    assert got == _cold(repo)


def test_another_gingugu_version_discards_the_cache(tmp_path, parses, monkeypatch):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    monkeypatch.setattr(gingugu, "__version__", "999.0.0")
    parses.clear()
    got = _warm(repo, cache)
    assert set(parses) == {r for r in FILES if lang.for_path(r) is not None}
    assert got == _cold(repo)


def test_an_unwritable_cache_still_answers(tmp_path):
    repo = make_repo(tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    assert _warm(repo, blocker / "parse.json") == _cold(repo)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_cache_file_is_owner_only(tmp_path):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    assert os.stat(cache).st_mode & 0o777 == 0o600


def test_no_cache_argument_writes_nothing(tmp_path):
    repo = make_repo(tmp_path)
    def files() -> set[Path]:
        return {p for p in tmp_path.rglob("*") if ".git" not in p.parts}  # git keeps its own

    before = files()
    extract.extract(repo)
    assert files() == before


def test_adapter_extract_still_equals_link_of_parse(tmp_path):
    """``extract`` stays on every adapter as the uncached path the lang tests use."""
    for adapter in lang.ADAPTERS:
        mine = {r: FILES[r].encode() for r in FILES if lang.for_path(r) is adapter}
        manifests = {r: FILES[r].encode() for r in FILES if r.endswith(("json", "toml", "mod"))}
        whole = adapter.extract(mine, manifests)
        raws = {r: json.loads(json.dumps(adapter.parse(r, b))) for r, b in mine.items()}
        linked = adapter.link(raws, manifests)
        assert (whole.files, whole.edges, whole.parse_errors) == (
            linked.files,
            linked.edges,
            linked.parse_errors,
        )


def test_the_hook_graph_reparses_only_the_edited_file(tmp_path, parses):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    hook_common.graph(repo, state)
    write(repo, "app/util.py", "import app.cli\n")
    parses.clear()
    g = hook_common.graph(repo, state)
    assert parses == ["app/util.py"]
    fresh = hook_common.graph(repo, tmp_path / "fresh-state")
    assert (g.files, sorted(g.links), g.ranks) == (fresh.files, sorted(fresh.links), fresh.ranks)
