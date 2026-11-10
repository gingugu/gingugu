"""X17 across languages: one symbol graph for the whole repo, whatever it is written in.

The X-ray law: a surface covers every language X-ray parses. Python, TS/JS and
Go each link their own symbols; the extraction merges them into one graph, so
the edit note's callers, the read note's "Most called" and ``code_xray
callers`` answer for a ``.ts`` or ``.go`` file exactly as for a ``.py`` one.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import edit_hook, edit_note, extract, hook_common, read_hook, symbols
from gingugu.xray.lang import golang, python, typescript
from tests.test_xray_edit_after import _after, _read
from tests.test_xray_edit_hook import _payload, _repo

MIXED = {
    "pkg/__init__.py": "",
    "pkg/core.py": "def f():\n    return 1\n\ndef g():\n    return f()\n",
    "web/src/lib.ts": (
        "export function f() {\n"  # 1
        "  return 1;\n"  # 2
        "}\n"  # 3
        "export const g = () => f();\n"  # 4
        "export function h() {\n"  # 5
        "  return f();\n"  # 6
        "}\n"  # 7
    ),
    "web/src/app.ts": (
        "import { $t } from './i18n';\nimport { h } from './lib';\n"
        "export function main() { $t('x'); h(); }\n"
    ),
    "web/src/lib.test.ts": "import { h } from './lib';\nit('h', () => { h(); });\n",
    "web/src/i18n.ts": "export function $t(k: string) {}\n",
    "svc/go.mod": "module example.com/svc\n",
    "svc/store/store.go": "package store\n\nfunc Helper() {}\n",
    "svc/main.go": (
        'package main\n\nimport "example.com/svc/store"\n\nfunc main() {\n\tstore.Helper()\n}\n'
    ),
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, MIXED)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


def test_the_extraction_merges_every_languages_symbols(repo):
    x = extract.extract(repo)
    e = x.symbols.edges
    assert ("pkg/core.py", "g", "pkg/core.py", "f") in e
    assert ("web/src/lib.ts", "g", "web/src/lib.ts", "f") in e
    assert ("web/src/app.ts", "main", "web/src/i18n.ts", "$t") in e
    assert ("svc/main.go", "main", "svc/store/store.go", "Helper") in e
    assert {"pkg/core.py", "web/src/lib.ts", "svc/store/store.go"} <= set(x.symbols.defs)


def test_the_merged_counts_are_the_sum_of_each_language(repo):
    sym = extract.extract(repo).symbols
    parts = []
    for adapter in (python.ADAPTER, typescript.ADAPTER, golang.ADAPTER):
        mine = {
            rel: text.encode() for rel, text in MIXED.items() if rel.endswith(adapter.extensions)
        }
        mf = {"svc/go.mod": MIXED["svc/go.mod"].encode()} if adapter is golang.ADAPTER else {}
        parts.append(adapter.extract(mine, mf).symbols)
    assert sym.resolved == sum(p.resolved for p in parts)
    assert sym.external == sum(p.external for p in parts)
    assert sym.unresolved == sum(p.unresolved for p in parts)
    assert sym.edges == {k: v for p in parts for k, v in p.edges.items()}


def test_a_cached_parse_gives_the_same_merged_symbols(repo, tmp_path):
    cache = tmp_path / "parse.json"
    cold = extract.extract(repo, parse_cache=cache)
    warm = extract.extract(repo, parse_cache=cache)
    assert warm.symbols == cold.symbols == extract.extract(repo).symbols


@pytest.mark.parametrize("lang", ["typescript", "go"])
def test_a_cached_parse_from_before_symbols_is_parsed_again(repo, tmp_path, lang):
    cache = tmp_path / "parse.json"
    extract.extract(repo, parse_cache=cache)
    data = json.loads(cache.read_text())
    for raw in data["raws"][lang].values():
        raw.pop("sym", None)  # the shape extractor version 3 wrote
    cache.write_text(json.dumps(data))
    again = extract.extract(repo, parse_cache=cache)
    assert again.symbols == extract.extract(repo).symbols


def test_the_brain_payload_still_never_carries_symbols(repo):
    sent = json.dumps(extract.extract(repo).payload())
    assert "$t" not in sent and "Helper" not in sent


def test_the_hook_cache_holds_every_language(repo, state):
    sym = hook_common.symbols(repo, state)
    assert ("web/src/app.ts", "main", "web/src/i18n.ts", "$t") in sym.edges
    assert hook_common.symbols(repo, state) == sym  # read back through from_json


def test_code_xray_callers_answers_for_typescript_and_go(repo, state):
    sym = hook_common.symbols(repo, state)
    ts = symbols.callers(sym, "web/src/lib.ts", "f")
    assert {d["symbol"] for d in ts["direct"]} == {"g", "h"}
    go = symbols.callers(sym, "svc/store/store.go", "Helper")
    assert go["direct"] == [{"path": "svc/main.go", "symbol": "main", "sites": 1}]


def test_a_typescript_read_names_its_most_called_definitions(repo, state):
    text = read_hook.check(_read(repo, "web/src/lib.ts"), state)
    assert "Most called: `f` (2 callers)" in text


def test_a_go_read_names_its_most_called_definitions(repo, state):
    text = read_hook.check(_read(repo, "svc/store/store.go"), state)
    assert "Most called: `Helper` (1 caller)" in text


def test_a_typescript_edit_names_the_definition_and_its_callers(repo, state):
    p = _payload(repo, "web/src/lib.ts")
    p["tool_input"].update(old_string="  return f();", new_string="  return f() + 1;")
    reason = edit_hook.check(p, state) or ""
    assert any(ln.startswith("Edit lands in `h` (L5-7)") for ln in reason.splitlines()), reason


def test_a_typescript_after_edit_names_cross_file_callers_and_their_tests(repo, state):
    read_hook.check(_read(repo, "web/src/lib.ts"), state)
    lib = repo / "web/src/lib.ts"
    lib.write_text(lib.read_text().replace("  return f();", "  return f() + 1;"))
    p = _after(repo, "web/src/lib.ts", old_string="  return f();", new_string="  return f() + 1;")
    text = edit_note.check(p, state) or ""
    line = next(ln for ln in text.splitlines() if ln.startswith("`h`"))
    assert "web/src/app.ts:main" in line
    assert "Tests: web/src/lib.test.ts" in line


def test_a_go_after_edit_names_cross_file_callers(repo, state):
    read_hook.check(_read(repo, "svc/store/store.go"), state)
    p = _after(repo, "svc/store/store.go", old_string="x", new_string="func Helper() {}")
    text = edit_note.check(p, state) or ""
    assert any(ln.startswith("`Helper`") and "svc/main.go:main" in ln for ln in text.splitlines())
