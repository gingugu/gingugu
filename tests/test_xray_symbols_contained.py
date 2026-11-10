"""A bug in one language's symbol linker costs that language's call graph, nothing more.

The call graph is a lower bound layered on the import graph. A linker that
raises on some repo shape must not take the import graph, or any other
language, down with it - and it must say so in the log, not vanish.
"""

from __future__ import annotations

import logging

import pytest

from gingugu.xray import extract
from gingugu.xray.lang import go_symbols, python, symbols_guard, ts_symbols
from tests.test_xray_edit_hook import _repo
from tests.test_xray_symbols_langs import MIXED


def _boom(*_a, **_k):
    raise TypeError("linker bug")


@pytest.mark.parametrize("cache", [False, True])
@pytest.mark.parametrize(
    "target, lost, kept",
    [
        ((go_symbols, "link"), "svc/main.go", ("pkg/core.py", "web/src/lib.ts")),
        ((ts_symbols, "link"), "web/src/lib.ts", ("pkg/core.py", "svc/main.go")),
        ((python.symbols, "link"), "pkg/core.py", ("web/src/lib.ts", "svc/main.go")),
    ],
)
def test_a_crashing_linker_costs_only_its_own_call_graph(
    tmp_path, monkeypatch, caplog, cache, target, lost, kept
):
    repo = _repo(tmp_path / "r", MIXED)
    healthy = extract.extract(repo)
    monkeypatch.setattr(*target, _boom)
    with caplog.at_level(logging.WARNING, logger=symbols_guard.__name__):
        x = extract.extract(repo, tmp_path / "pc" if cache else None)

    srcs = {k[0] for k in x.symbols.edges} | set(x.symbols.defs)
    assert lost not in srcs
    for path in kept:
        assert path in srcs
    # The import graph is untouched: same files, same edges, nothing unparsed.
    assert [f.path for f in x.files] == [f.path for f in healthy.files]
    assert x.edges == healthy.edges
    assert x.parse_errors == healthy.parse_errors
    assert "linker bug" in caplog.text


def test_a_healthy_link_passes_through_untouched():
    sentinel = object()
    assert symbols_guard.contained("go", lambda a, b: (a, b, sentinel), 1, 2) == (1, 2, sentinel)
