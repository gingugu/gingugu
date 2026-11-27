"""The CI shard filter (tests/sharding.py): stable, whole-file, and loud on bad input."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import sharding


def test_every_file_lands_in_exactly_one_shard() -> None:
    files = sorted(p.as_posix() for p in Path("tests").glob("test_*.py"))
    for total in (2, 3):
        shards = [sharding.shard_of(f"{f}::test_x", total) for f in files]
        assert set(shards) <= set(range(1, total + 1))
        assert len(shards) == len(files)


def test_a_file_is_never_split_across_shards() -> None:
    a = sharding.shard_of("tests/test_board.py::test_one", 2)
    b = sharding.shard_of("tests/test_board.py::TestX::test_two[param]", 2)
    assert a == b


def test_the_split_is_roughly_even_on_this_suite() -> None:
    files = sorted(p.as_posix() for p in Path("tests").glob("test_*.py"))
    first = sum(sharding.shard_of(f, 2) == 1 for f in files)
    assert 0.35 < first / len(files) < 0.65


@pytest.mark.parametrize("spec", ["", "1", "0/2", "3/2", "a/b", "1/2/3", "-1/2"])
def test_a_bad_spec_is_an_error_not_a_full_run(spec: str) -> None:
    with pytest.raises(ValueError, match="GINGUGU_TEST_SHARD"):
        sharding.parse(spec)


def test_a_good_spec_parses() -> None:
    assert sharding.parse("2/3") == (2, 3)


class _Item:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


class _Config:
    def __init__(self) -> None:
        self.deselected: list = []
        self.hook = self

    def pytest_deselected(self, items) -> None:
        self.deselected.extend(items)


def test_the_hook_keeps_its_shard_and_reports_the_rest(monkeypatch) -> None:
    items = [_Item(f"tests/test_{i}.py::t") for i in range(40)]
    kept_total = 0
    for index in (1, 2):
        monkeypatch.setenv(sharding.ENV, f"{index}/2")
        got, config = list(items), _Config()
        sharding.pytest_collection_modifyitems(config, got)
        assert all(sharding.shard_of(i.nodeid, 2) == index for i in got)
        assert len(got) + len(config.deselected) == len(items)
        kept_total += len(got)
    assert kept_total == len(items)


def test_unset_filters_nothing(monkeypatch) -> None:
    monkeypatch.delenv(sharding.ENV, raising=False)
    items = [_Item("tests/test_a.py::t")]
    sharding.pytest_collection_modifyitems(_Config(), items)
    assert len(items) == 1
