"""X16 parse cache under a bad cache or a hostile repo: still the cold answer, never a crash.

From the security review of the parse cache: a cached record whose fields are
the wrong JSON type, two files whose names are only an extension, a cache too
big to be worth keeping, and a parser dependency that changed under the same
gingugu version.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import extract, lang, parse_cache
from tests.xray_incremental_fixtures import make_repo, write


def _cold(repo):
    return extract.extract(repo).payload()


def _warm(repo, cache):
    return extract.extract(repo, parse_cache=cache).payload()


@pytest.fixture
def parses(monkeypatch) -> list[str]:
    seen: list[str] = []
    for adapter in lang.ADAPTERS:
        orig = adapter.parse

        def spy(rel, data, _orig=orig):
            seen.append(rel)
            return _orig(rel, data)

        monkeypatch.setattr(adapter, "parse", spy)
    return seen


def _poison(cache, adapter: str, raw: object) -> None:
    data = json.loads(cache.read_text())
    for key in data["raws"][adapter]:
        data["raws"][adapter][key] = raw
    cache.write_text(json.dumps(data))


@pytest.mark.parametrize(
    "adapter,raw",
    [
        ("python", {"err": False, "main": False, "dyn": 0, "recs": [["i", [], "x"]]}),
        ("python", {"err": False, "main": False, "dyn": 0, "recs": [["f", {}, 0, "", []]]}),
        ("typescript", {"specs": [["a", []]], "dyn": 0, "err": False}),
        ("typescript", {"specs": [["a", {"k": 1}]], "dyn": 0, "err": False}),
        ("go", {"pkg": [], "imports": [[]], "err": False}),
    ],
)
def test_an_unhashable_field_in_a_cached_record_is_reparsed_not_raised(tmp_path, adapter, raw):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    _poison(cache, adapter, raw)
    assert _warm(repo, cache) == _cold(repo)


def test_files_named_only_an_extension_keep_their_own_grammar(tmp_path):
    """``.ts`` and ``.tsx`` as whole names have no suffix, but parse differently."""
    jsx = "const a = <div/>;\n"
    repo = make_repo(tmp_path, {"web/.ts": jsx, "web/x/.tsx": jsx})
    for order in (["web/.ts", "web/x/.tsx"], ["web/x/.tsx", "web/.ts"]):
        cache = tmp_path / f"parse-{order[0].replace('/', '_')}.json"
        for rel in order:  # seed the cache with one name first, then the other
            (repo / rel).rename(repo / f"{rel}.off")
        for rel in order:
            (repo / f"{rel}.off").rename(repo / rel)
            _warm(repo, cache)
        assert _warm(repo, cache) == _cold(repo)
    errors = set(_cold(repo)["parse_errors"])
    assert "web/.ts" in errors and "web/x/.tsx" not in errors  # the two really differ


def test_a_cache_past_the_size_cap_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(parse_cache, "MAX_BYTES", 100)
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    assert _warm(repo, cache) == _cold(repo)
    assert not cache.exists()


def test_a_parser_dependency_upgrade_discards_the_cache(tmp_path, parses, monkeypatch):
    repo = make_repo(tmp_path)
    cache = tmp_path / "parse.json"
    _warm(repo, cache)
    real = parse_cache.metadata.version
    monkeypatch.setattr(
        parse_cache.metadata,
        "version",
        lambda name: "99.0" if name == "tree-sitter-go" else real(name),
    )
    write(repo, "web/src/lazy.ts", "export default 2;\n")
    parses.clear()
    got = _warm(repo, cache)
    assert {r for r in parses if r.endswith(".go")}  # every file re-parsed, Go included
    assert got == _cold(repo)
