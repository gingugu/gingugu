"""Hostile repos: an untrusted checkout must not hang, crash or sink an X-ray.

Each case is from a security review of the adapters. The analysed repo is
untrusted; the worst it may do is make its own graph less complete.
"""

from __future__ import annotations

import time

import pytest

from gingugu.xray import extract, validate
from gingugu.xray.lang.golang import ADAPTER as GO
from gingugu.xray.lang.ts_config import leaves
from gingugu.xray.lang.typescript import ADAPTER as TS
from tests.xray_lang_fixtures import _repo, edges, run_adapter


def _fast(fn, limit: float = 2.0):
    start = time.perf_counter()
    out = fn()
    assert time.perf_counter() - start < limit
    return out


def test_an_extends_diamond_is_linear_not_exponential():
    n = 30
    configs = {
        ("tsconfig.json" if i == 0 else f"tsconfig{i}.json"): (
            f'{{"extends": ["./tsconfig{i + 1}", "./tsconfig{i + 1}.json"]}}'
        )
        for i in range(n)
    }
    # The alias sits inside MAX_EXTENDS; the 20 levels past it still must not hang.
    configs["tsconfig10.json"] = (
        '{"extends": ["./tsconfig11", "./tsconfig11.json"],'
        ' "compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'
    )
    configs[f"tsconfig{n}.json"] = "{}"
    res = _fast(lambda: run_adapter(TS, {"a.ts": "import x from '@/x'\n", "src/x.ts": ""}, configs))
    assert ("a.ts", "src/x.ts") in edges(res)


def test_a_very_long_extends_chain_neither_recurses_out_nor_hangs():
    n = 1100
    configs = {
        ("tsconfig.json" if i == 0 else f"tsconfig{i:04}.json"): (
            f'{{"extends": "./tsconfig{i + 1:04}.json"}}'
        )
        for i in range(n)
    }
    configs[f"tsconfig{n:04}.json"] = "{}"
    res = _fast(lambda: run_adapter(TS, {"a.ts": "import r from 'react'\n"}, configs))
    assert res.external == 1


def test_a_huge_paths_map_and_a_huge_specifier_stay_bounded():
    targets = ",".join(['"*"'] * 100_000)
    tsconfig = f'{{"compilerOptions": {{"paths": {{"*": [{targets}]}}}}}}'
    src = "import x from '" + "a" * 200_000 + "'\n"
    res = _fast(lambda: run_adapter(TS, {"a.ts": src}, {"tsconfig.json": tsconfig}), 5.0)
    assert res.external == 1


def test_deep_exports_do_not_recurse_out():
    deep = "[" * 700 + '"./x.js"' + "]" * 700
    assert leaves([[["a"]]]) == ["a"]
    run_adapter(
        TS, {"a.ts": "", "x.ts": ""}, {"package.json": f'{{"name": "p", "exports": {deep}}}'}
    )


def test_nested_unclosed_script_openers_are_linear():
    html = "<script " * 200_000 + ">"
    _fast(lambda: run_adapter(TS, {"a.ts": ""}, {"index.html": html}))


@pytest.mark.parametrize("mod", ["a\x00b", "a" * 2000, "a\x1b[31mred"])
def test_a_go_module_path_the_brain_would_refuse_is_malformed(mod):
    res = run_adapter(GO, {"a.go": "package a\n"}, {"go.mod": f"module {mod}\n"})
    assert ("go.mod", "malformed") in res.skipped
    assert all(f.kind == "file" for f in res.files)


def test_a_go_package_named_like_a_real_file_never_shadows_it():
    res = run_adapter(
        GO,
        {"main.go": 'package main\nimport "cmd/main.go"\n', "cmd/main.go": "package main\n"},
        {"go.mod": "module cmd/main.go\n"},
    )
    assert [f.kind for f in res.files if f.path == "cmd/main.go"] == ["file"]
    assert ("cmd/main.go", "cmd/main.go") not in edges(res)


def test_an_adapter_recursing_out_costs_only_its_own_language(tmp_path, monkeypatch):
    def boom(self, sources, manifests):
        raise RecursionError

    monkeypatch.setattr(type(TS), "extract", boom)
    x = extract.extract(_repo(tmp_path, {"a.py": "import b\n", "b.py": "", "c.ts": ""}))
    assert ("a.py", "b.py") in {(e.src, e.dst) for e in x.edges}
    assert x.parse_errors == ["c.ts"]
    validate.payload(x.payload())


def test_a_node_name_the_brain_would_refuse_is_never_shipped(tmp_path, fake, monkeypatch):
    from gingugu.xray.model import FileInfo
    from tests.xray_lang_fixtures import FakeAdapter

    real = FakeAdapter.extract

    def bad(self, sources, manifests):
        out = real(self, sources, manifests)
        out.files.append(FileInfo("../escape", False, False, "fake", "package"))
        return out

    monkeypatch.setattr(FakeAdapter, "extract", bad)
    x = extract.extract(_repo(tmp_path, {"a.fk": ""}))
    assert all(f.path != "../escape" for f in x.files)
    validate.payload(x.payload())


def test_past_the_total_byte_cap_files_are_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "MAX_TOTAL_BYTES", 10)
    x = extract.extract(_repo(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"}))
    assert [f.path for f in x.files] == ["a.py"]
    assert ("b.py", "too_large") in x.skipped
