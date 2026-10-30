"""Which tests to name first for a change to one file.

Rank says little about tests, and hops alone tie constantly: in gingugu a change
to ``grants.py`` reaches 171 tests, dozens of them one hop away, and an
alphabetical tiebreak put ``test_aboutness.py`` and ``test_board.py`` ahead of
``test_grants.py``. The test written for a file is usually named for it, so a
test whose name carries the file's stem as whole words goes first - one named
``test_<stem>`` before one that merely contains it - then the rest by hops.
"""

from __future__ import annotations

from gingugu.xray.hook_common import nearest_tests


def _rows(*pairs):
    return [{"path": p, "hops": h} for p, h in pairs]


def test_a_test_named_for_the_file_goes_first_even_when_further_away():
    rows = _rows(
        ("tests/test_aboutness.py", 1),
        ("tests/test_board.py", 1),
        ("tests/test_scoped_grants.py", 2),
        ("tests/test_grants.py", 3),
    )
    assert nearest_tests(rows, seed="src/gingugu/grants.py") == [
        "tests/test_grants.py",
        "tests/test_scoped_grants.py",
        "tests/test_aboutness.py",
        "tests/test_board.py",
    ]


def test_the_stem_matches_whole_words_only():
    rows = _rows(("tests/test_aboutness.py", 1), ("tests/test_a_thing.py", 2))
    assert nearest_tests(rows, seed="pkg/a.py") == [
        "tests/test_a_thing.py",
        "tests/test_aboutness.py",
    ]


def test_a_multi_word_stem_matches_in_order():
    rows = _rows(
        ("tests/test_common_hook.py", 1),
        ("tests/test_xray_hook_common.py", 2),
        ("tests/test_hook_common_cache.py", 3),
    )
    assert nearest_tests(rows, seed="src/x/hook_common.py") == [
        "tests/test_hook_common_cache.py",
        "tests/test_xray_hook_common.py",
        "tests/test_common_hook.py",
    ]


def test_a_package_init_is_named_for_its_directory():
    rows = _rows(("tests/test_other.py", 1), ("tests/test_migrations.py", 2))
    assert nearest_tests(rows, seed="src/gingugu/migrations/__init__.py")[0] == (
        "tests/test_migrations.py"
    )


def test_without_a_seed_it_is_test_files_then_hops_then_path():
    rows = _rows(("tests/helpers.py", 1), ("tests/test_b.py", 2), ("tests/test_a.py", 2))
    assert nearest_tests(rows) == ["tests/test_a.py", "tests/test_b.py", "tests/helpers.py"]


def test_a_helper_is_never_lifted_above_a_test_by_its_name():
    rows = _rows(("tests/grants_fixtures.py", 1), ("tests/test_other.py", 2))
    assert nearest_tests(rows, seed="src/grants.py")[0] == "tests/test_other.py"
