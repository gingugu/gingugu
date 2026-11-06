"""X-ray literal consumers, unquoted keys (board d5639dd5, his call 2026-11-05).

A key need not be quoted to be a data contract: ``emit(status="ok")`` and a TS
``{status: x}`` both write a ``status`` field. Detection reads each language's
own form only; readers are searched in tests, docs and config, never in non-test
code, where the symbol graph already follows a renamed keyword.
"""

from __future__ import annotations

import pytest

from gingugu.xray import literals
from tests.test_xray_edit_hook import _repo

# --- changed_bare: Python keyword arguments -----------------------------------------


def test_a_renamed_kwarg_in_a_call_is_removed():
    old = 'log.info("sync", run=n, status="ok")'
    new = 'log.info("sync", run=n, outcome="ok")'
    assert literals.changed_bare([old], [new], "sync/cli.py") == ["status"]


def test_a_dict_kwarg_is_removed():
    assert literals.changed_bare(["dict(status=1)"], ["dict(outcome=1)"], "a.py") == ["status"]


def test_a_kwarg_on_its_own_line_inside_a_call_is_removed():
    old = "emit(\n    run=n,\n    status=s,\n)"
    new = "emit(\n    run=n,\n    outcome=s,\n)"
    assert literals.changed_bare([old], [new], "a.py") == ["status"]


@pytest.mark.parametrize("old", ["status = 1", "if status == 1: pass", "x = status"])
def test_an_assignment_or_comparison_is_no_key(old):
    assert literals.changed_bare([old], ["pass"], "a.py") == []


def test_a_python_annotation_is_no_key():
    assert literals.changed_bare(["def g(a, status: int): ..."], ["def g(a): ..."], "a.py") == []


def test_a_kwarg_kept_by_the_new_text_is_not_removed():
    assert literals.changed_bare(["f(status=1, run=2)"], ["f(run=2, status=1)"], "a.py") == []


# --- changed_bare: TS/JS object-literal keys ----------------------------------------


@pytest.mark.parametrize("rel", ["web/a.ts", "web/a.tsx", "web/a.js", "web/a.jsx", "a.mjs"])
def test_an_inline_object_key_is_removed(rel):
    assert literals.changed_bare(["send({status: s, run})"], ["send({outcome: s, run})"], rel) == [
        "status"
    ]


def test_a_key_leading_a_line_inside_an_object_is_removed():
    old = "const line = {\n  run: n,\n  status: s,\n}"
    new = "const line = {\n  run: n,\n  outcome: s,\n}"
    assert literals.changed_bare([old], [new], "web/a.ts") == ["status"]


@pytest.mark.parametrize("old", ["a ? status : b", "case status:", "x = status"])
def test_a_ternary_case_or_assignment_is_no_key(old):
    assert literals.changed_bare([old], ["x"], "web/a.ts") == []


# --- changed_bare: each language reads only its own form ----------------------------


def test_python_does_not_read_object_keys():
    assert literals.changed_bare(["{status: x}"], ["{}"], "a.py") == []


def test_ts_does_not_read_kwargs():
    assert literals.changed_bare(["f(status=1)"], ["f()"], "web/a.ts") == []


def test_another_language_names_no_bare_key():
    assert literals.changed_bare(["f(status=1)", "{status: 1}"], [""], "main.go") == []


def test_the_noise_rule_holds_for_bare_keys():
    old = "f(id=1, self=2, status=3)"
    assert literals.changed_bare([old], ["f()"], "a.py") == ["status"]


def test_at_most_max_literals_bare_keys():
    keys = [f"key_{i}" for i in range(literals.MAX_LITERALS + 2)]
    old = "f(" + ", ".join(f"{k}=1" for k in keys) + ")"
    assert literals.changed_bare([old], ["f()"], "a.py") == keys[: literals.MAX_LITERALS]


# --- consumers(bare=True): tests, docs and config only ------------------------------

BARE = {
    "sync/cli.py": "emit(status='ok')\n",
    "tests/test_cli.py": "def test_x():\n    assert run().status == 'ok'\n    emit(status='ok')\n",
    "fixtures/line.json": '{"status": "ok"}\n',
    "docs/runbook.md": "Pass `status` to emit.\n",
    "docs/prose.md": "The status of the run, and its statuses.\n",
    "monitors/sync.yaml": "status: enabled\n",
    "app/other.py": "emit(status='bad')\nrow['status']\n",
    "web/form.ts": "const x = {status: 1}\n",
}


@pytest.fixture
def bare(tmp_path):
    return _repo(tmp_path, BARE)


def _paths(found):
    return [p for p, _ in found.hits]


def test_bare_readers_are_tests_docs_and_config_only(bare):
    found = literals.consumers(bare, "status", exclude="sync/cli.py", bare=True)
    assert _paths(found) == [
        "fixtures/line.json",
        "tests/test_cli.py",
        "tests/test_cli.py",
        "docs/runbook.md",
        "monitors/sync.yaml",
    ]
    assert found.files == 4


def test_a_test_reads_a_bare_key_by_attribute_and_by_kwarg(bare):
    found = literals.consumers(bare, "status", exclude="sync/cli.py", bare=True)
    assert ("tests/test_cli.py", 2) in found.hits  # .status
    assert ("tests/test_cli.py", 3) in found.hits  # status=


def test_non_test_code_is_left_to_the_symbol_graph(bare):
    paths = _paths(literals.consumers(bare, "status", exclude="sync/cli.py", bare=True))
    assert "app/other.py" not in paths and "web/form.ts" not in paths


def test_prose_naming_the_word_without_a_delimiter_is_no_reader(bare):
    paths = _paths(literals.consumers(bare, "status", exclude="sync/cli.py", bare=True))
    assert "docs/prose.md" not in paths


def test_the_quoted_search_still_reads_code(bare):
    paths = _paths(literals.consumers(bare, "status", exclude="sync/cli.py"))
    assert "app/other.py" in paths
