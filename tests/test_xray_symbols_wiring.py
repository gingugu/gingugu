"""X17 wired in: the symbol graph rides the extraction, the hook cache, both hooks and code_xray.

Client-side only: the symbol graph is built where the checkout is, cached beside
the hooks' import graph, and never sent to the brain - ``Extraction.payload``
is unchanged. The edit tripwire names the definitions an edit lands in and
their callers; the read neighbourhood names a file's most-called definitions;
``code_xray`` action ``callers`` answers for one symbol. Call counts are a
lower bound and every surface says so.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import edit_hook, extract, hook_common, read_hook, symbols
from tests.test_xray_edit_hook import _payload, _repo

SYM = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "def f():\n"  # 1
        "    return 1\n"  # 2
        "\n"  # 3
        "class Store:\n"  # 4
        "    def save(self):\n"  # 5
        "        return f()\n"  # 6
        "\n"  # 7
        "def unused():\n"  # 8
        "    pass\n"  # 9
    ),
    "pkg/svc.py": (
        "from pkg.core import f, Store\n\n"
        "def serve():\n    return f()\n\n"
        "def keep(s: Store):\n    s.save()\n"
    ),
    "pkg/cli.py": "from pkg import svc\n\ndef main():\n    svc.serve()\n",
    "pkg/lone.py": "def a():\n    return b()\n\ndef b():\n    return 1\n",
    "tests/test_svc.py": "from pkg import svc\n\ndef test_serve():\n    svc.serve()\n",
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, SYM)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


# --- extraction ----------------------------------------------------------------


def test_the_extractor_version_moves_so_every_cache_rebuilds():
    assert extract.EXTRACTOR_VERSION == 3


def test_an_extraction_carries_the_symbol_graph(repo):
    x = extract.extract(repo)
    assert isinstance(x.symbols, symbols.Symbols)
    assert ("pkg/svc.py", "serve", "pkg/core.py", "f") in x.symbols.edges
    assert x.symbols.defs["pkg/core.py"]["Store.save"] == ("method", 5, 6)


def test_the_brain_payload_never_carries_symbols(repo):
    sent = extract.extract(repo).payload()
    assert set(sent) == {
        "fingerprint",
        "extractor_version",
        "commit",
        "files",
        "edges",
        "unresolved",
        "dynamic",
        "external",
        "parse_errors",
        "skipped",
    }
    assert sent["extractor_version"] == 3


def test_a_cached_parse_gives_the_same_symbols(repo, tmp_path):
    cache = tmp_path / "parse.json"
    cold = extract.extract(repo, parse_cache=cache)
    warm = extract.extract(repo, parse_cache=cache)
    assert warm.symbols == cold.symbols == extract.extract(repo).symbols


def test_a_cached_parse_without_symbol_records_is_parsed_again(repo, tmp_path):
    cache = tmp_path / "parse.json"
    extract.extract(repo, parse_cache=cache)
    data = json.loads(cache.read_text())
    for raw in data["raws"]["python"].values():
        raw.pop("sym", None)  # the shape an older extractor wrote
    cache.write_text(json.dumps(data))
    again = extract.extract(repo, parse_cache=cache)
    assert ("pkg/svc.py", "serve", "pkg/core.py", "f") in again.symbols.edges


def test_a_file_that_does_not_parse_has_no_definitions(repo):
    (repo / "pkg/broken.py").write_text("def (:\n")
    x = extract.extract(repo)
    assert x.symbols.defs.get("pkg/broken.py", {}) == {}
    assert "pkg/broken.py" in x.parse_errors


# --- the hook cache ------------------------------------------------------------


def test_the_hooks_cache_the_symbol_graph_beside_the_import_graph(repo, state, monkeypatch):
    sym = hook_common.symbols(repo, state)
    assert ("pkg/svc.py", "serve", "pkg/core.py", "f") in sym.edges
    assert list(state.glob("xray-symbols-*.json"))

    def boom(*a, **k):
        raise AssertionError("rebuilt a graph that was cached")

    monkeypatch.setattr(extract, "extract", boom)
    assert hook_common.symbols(repo, state) == sym
    hook_common.graph(repo, state)  # one build served both


def test_a_changed_checkout_rebuilds_the_symbol_cache(repo, state):
    hook_common.symbols(repo, state)
    (repo / "pkg/new.py").write_text("from pkg.core import f\n\ndef g():\n    f()\n")
    assert ("pkg/new.py", "g", "pkg/core.py", "f") in hook_common.symbols(repo, state).edges


def test_a_corrupt_symbol_cache_is_rebuilt(repo, state):
    hook_common.symbols(repo, state)
    for p in state.glob("xray-symbols-*.json"):
        data = json.loads(p.read_text())
        data["defs"] = 7  # right fingerprint, wrong shape: untrusted all the same
        p.write_text(json.dumps(data))
    assert ("pkg/svc.py", "serve", "pkg/core.py", "f") in hook_common.symbols(repo, state).edges


# --- the edit tripwire ---------------------------------------------------------


def _edit(repo, rel, old, tool="Edit", session="cc-1"):
    out = _payload(repo, rel, tool=tool, session=session)
    out["tool_input"]["old_string"] = old
    out["tool_input"]["new_string"] = old + "  # x"
    return out


def test_an_edit_names_the_definition_it_lands_in_and_its_callers(repo, state):
    reason = edit_hook.check(_edit(repo, "pkg/core.py", "    return 1"), state)
    assert "is imported by" in reason  # the file blast is still there
    line = next(ln for ln in reason.splitlines() if ln.startswith("Edit lands in"))
    assert line == (
        "Edit lands in `f` (L1-2): 2 callers - pkg/core.py:Store.save, pkg/svc.py:serve; "
        "reaches 5 definitions in 4 files."
    )
    assert "lower bound" in reason
    assert reason.splitlines()[-1].startswith("Import-based estimate")  # the close stays last


def test_a_multi_edit_names_every_definition_it_touches(repo, state):
    p = _payload(repo, "pkg/core.py", tool="MultiEdit")
    p["tool_input"]["edits"] = [
        {"old_string": "    return 1", "new_string": "    return 2"},
        {"old_string": "        return f()", "new_string": "        return f() + 1"},
    ]
    reason = edit_hook.check(p, state)
    assert "`f` (L1-2)" in reason and "`Store.save` (L5-6)" in reason


def test_a_definition_nobody_calls_says_so(repo, state):
    reason = edit_hook.check(_edit(repo, "pkg/core.py", "    pass"), state)
    assert "`unused` (L8-9): no resolved callers" in reason


def test_an_edit_outside_any_definition_adds_nothing(repo, state):
    reason = edit_hook.check(_edit(repo, "pkg/svc.py", "from pkg.core import f"), state)
    assert reason is not None and "Edit lands in" not in reason
    assert "lower bound" not in reason


def test_an_old_string_that_is_not_in_the_file_adds_nothing(repo, state):
    reason = edit_hook.check(_edit(repo, "pkg/core.py", "not there at all"), state)
    assert reason is not None and "Edit lands in" not in reason


def test_a_write_adds_no_symbol_lines(repo, state):
    reason = edit_hook.check(_payload(repo, "pkg/core.py", tool="Write"), state)
    assert reason is not None and "Edit lands in" not in reason


def test_symbol_callers_do_not_change_when_the_tripwire_fires(repo, state):
    # lone.py: a calls b, but nothing imports the file - silent, as before X17
    assert edit_hook.check(_edit(repo, "pkg/lone.py", "    return 1"), state) is None


def test_at_most_three_definitions_are_named(tmp_path, state):
    body = "".join(f"def f{i}():\n    return {i}\n" for i in range(6))
    repo = _repo(tmp_path, {"m.py": body, "u.py": "import m\n"})
    reason = edit_hook.check(_edit(repo, "m.py", body), state)
    assert reason.count("Edit lands in") == 3
    assert "(+3 more definitions)" in reason


# --- the read neighbourhood -----------------------------------------------------


def _read(repo, rel, session="cc-1"):
    out = _payload(repo, rel, tool="Read", session=session)
    out["hook_event_name"] = "PostToolUse"
    return out


def test_a_read_names_the_files_most_called_definitions(repo, state):
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    near = text.split("\n\n")[-1]
    assert "Most called: `f` (2 callers), `Store.save` (1 caller)" in near
    assert len(near.splitlines()) <= 5


def test_a_file_whose_definitions_nobody_calls_has_no_most_called_line(repo, state):
    text = read_hook.check(_read(repo, "pkg/cli.py"), state)
    assert "Most called" not in text
