"""X-ray literal consumers (board d5639dd5): the text readers of a removed quoted literal.

The import graph cannot see a data contract: nothing imports the ``"status"`` key
of a JSON line, yet tests, runbooks and monitor queries read it. These pin which
literals an edit is taken to have removed, which of those are worth a sweep, and
where the sweep finds them.
"""

from __future__ import annotations

import pytest

from gingugu.xray import literals
from gingugu.xray.scan import XrayError
from tests.test_xray_edit_hook import _repo

# --- changed: which quoted literals an edit removed ---------------------------------


def test_a_renamed_dict_key_is_the_removed_literal():
    old = 'print(json.dumps({"status": "ok", "run": n}))'
    new = 'print(json.dumps({"outcome": "ok", "run": n}))'
    assert literals.changed([old], [new]) == ["status"]


def test_single_quotes_count_the_same():
    assert literals.changed(["row['status']"], ["row['outcome']"]) == ["status"]


def test_a_literal_the_new_text_still_quotes_was_moved_not_removed():
    old = 'a = {"status": 1}\nb = 2'
    new = 'b = 2\na = {"status": 1}'
    assert literals.changed([old], [new]) == []


def test_a_literal_kept_by_any_new_string_of_a_multiedit_is_not_removed():
    assert literals.changed(['x["status"]', 'y["run"]'], ['x["outcome"]', 'y["status"]']) == ["run"]


def test_first_seen_first_each_once():
    old = '{"beta": 1, "alpha": 2, "beta": 3}'
    assert literals.changed([old], ["{}"]) == ["beta", "alpha"]


def test_at_most_max_literals_per_edit():
    keys = [f"key_{i}" for i in range(literals.MAX_LITERALS + 4)]
    old = "{" + ", ".join(f'"{k}": 1' for k in keys) + "}"
    assert literals.changed([old], ["{}"]) == keys[: literals.MAX_LITERALS]


def test_an_unquoted_word_is_no_literal():
    assert literals.changed(["status = 1"], ["outcome = 1"]) == []


def test_no_old_strings_names_nothing():
    assert literals.changed([], ['"anything"']) == []


# --- qualifies: the noise rule ------------------------------------------------------


@pytest.mark.parametrize("lit", ["status", "price_sync_run", "x-request-id", "app.name", "_id"])
def test_identifier_shaped_literals_qualify(lit):
    assert literals.qualifies(lit)


@pytest.mark.parametrize(
    "lit",
    [
        "id",  # under MIN_LEN
        "a" * (literals.MAX_LEN + 1),
        "two words",
        "path/to/file.py",
        "https://example.com",
        "{name}",
        "%s",
        "",
        "9lives",  # must start with a letter or underscore
    ],
)
def test_prose_paths_formats_and_tiny_strings_do_not(lit):
    assert not literals.qualifies(lit)


@pytest.mark.parametrize("lit", ["utf-8", "true", "None", "NULL", "delete", "__main__"])
def test_common_values_are_stopped_case_insensitively(lit):
    assert not literals.qualifies(lit)


def test_changed_drops_what_does_not_qualify():
    old = '{"id": 1, "status": "utf-8", "a b": 2}'
    assert literals.changed([old], ["{}"]) == ["status"]


# --- consumers: where the literal is still named ------------------------------------

CONTRACT = {
    "sync/cli.py": 'print(json.dumps({"status": "ok"}))\n',
    "tests/test_cli.py": 'def test_x(lines):\n    assert lines[-1]["status"] == "ok"\n',
    "docs/runbook.md": "Check the `status` field of the last line.\n",
    "monitors/sync.yaml": 'query: logs("service:sync @status:error")\nstatus: enabled\n',
    "fixtures/line.json": '{"status": "ok"}\n',
    "app/other.py": "def f(row):\n    return row['status']\n",
    "app/noise.py": "statuses = 1\nmy_status = 2\nstatus_code = 3\nsubstatus = 4\n",
    "app/annot.py": "def g(status: int) -> None: ...\n",
    "uv.lock": '"status"\n',
    "web/app.min.js": '"status"\n',
}


@pytest.fixture
def contract(tmp_path):
    return _repo(tmp_path, CONTRACT)


def _paths(found):
    return [p for p, _ in found.hits]


def test_every_delimited_reader_is_found_and_the_edited_file_is_not(contract):
    found = literals.consumers(contract, "status", exclude="sync/cli.py")
    assert set(_paths(found)) == {
        "tests/test_cli.py",
        "docs/runbook.md",
        "monitors/sync.yaml",
        "fixtures/line.json",
        "app/other.py",
    }
    assert found.files == 5


def test_hits_carry_the_line_number(contract):
    found = literals.consumers(contract, "status", exclude="sync/cli.py")
    assert ("tests/test_cli.py", 2) in found.hits


def test_a_longer_word_containing_the_literal_is_no_reader(contract):
    found = literals.consumers(contract, "status", exclude="sync/cli.py")
    assert "app/noise.py" not in _paths(found)


def test_a_datadog_facet_query_alone_is_a_reader(tmp_path):
    # The case that started this (board d5639dd5): a monitor that only ever
    # names the key as a facet, `@status:error`, with no YAML key beside it.
    repo = _repo(
        tmp_path,
        {
            "sync/cli.py": 'print(json.dumps({"status": "ok"}))\n',
            "monitors/only_facet.yaml": 'query: logs("service:sync @status:error").rollup()\n',
        },
    )
    found = literals.consumers(repo, "status", exclude="sync/cli.py")
    assert ("monitors/only_facet.yaml", 1) in found.hits


def test_a_bare_key_colon_counts_only_outside_code(contract):
    # ``status: int`` is a Python annotation; ``status: enabled`` is a YAML key.
    found = literals.consumers(contract, "status", exclude="sync/cli.py")
    assert "app/annot.py" not in _paths(found)
    assert ("monitors/sync.yaml", 2) in found.hits


def test_lockfiles_and_generated_files_are_skipped(contract):
    found = literals.consumers(contract, "status", exclude="sync/cli.py")
    assert "uv.lock" not in _paths(found)
    assert "web/app.min.js" not in _paths(found)


def test_tests_and_fixtures_lead_then_docs_then_other_non_code_then_code(contract):
    # Groups by ``links.model``: ``is_test`` (fixtures/ included), ``is_doc``, CODE_EXTS.
    order = _paths(literals.consumers(contract, "status", exclude="sync/cli.py"))
    assert order == [
        "fixtures/line.json",
        "tests/test_cli.py",
        "docs/runbook.md",
        "monitors/sync.yaml",
        "app/other.py",
    ]


def test_an_untracked_reader_is_found(contract):
    (contract / "tests" / "test_new.py").write_text('assert row["status"]\n')
    found = literals.consumers(contract, "status", exclude="sync/cli.py")
    assert "tests/test_new.py" in _paths(found)


def test_a_literal_nobody_names_finds_nothing(contract):
    assert literals.consumers(contract, "price_sync_run", exclude="sync/cli.py") == literals.Found(
        [], 0
    )


def test_a_git_failure_raises_xray_error(tmp_path):
    with pytest.raises(XrayError):
        literals.consumers(tmp_path, "status", exclude="x.py")  # not a repo
