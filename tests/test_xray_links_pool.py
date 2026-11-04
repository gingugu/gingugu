"""X23: cold extraction on worker processes gives the same facts, and only when asked.

A deliberate read (budget >= pool.MIN_BUDGET_S, default extractors) with enough cold
files hands them to a process pool; anything else extracts in-process.
"""

from __future__ import annotations

from gingugu.xray.links import index, pool
from tests.test_xray_links_index import eco  # noqa: F401 - the fixture


def _many(a, n=80):
    for i in range(n):
        (a / f"svc/m{i}.py").write_text(f'URL = "https://svc-{i}.acme.io/internal/route-{i}"\n')


def test_a_pool_read_finds_what_an_in_process_read_does(eco, tmp_path, monkeypatch):  # noqa: F811
    a, state = eco
    _many(a)
    started = []
    real_init = pool.Pool.__init__

    def spy(self, enabled):
        real_init(self, enabled)
        started.append(enabled)

    monkeypatch.setattr(pool.Pool, "__init__", spy)
    pooled = index.read(a, state, budget_s=30)
    serial_state = tmp_path / "serial"
    serial = index.read(a, serial_state, budget_s=30, extract=index._default_extract)
    assert started == [True, False]  # the default read asked for a pool; a passed extractor not
    key = lambda f: (f.kind, f.key, f.path, f.line, f.detail, f.target)  # noqa: E731
    for rid in serial.repos:
        assert sorted(map(key, pooled.repos[rid].mentioned)) == sorted(
            map(key, serial.repos[rid].mentioned)
        )
        assert sorted(map(key, pooled.repos[rid].provided)) == sorted(
            map(key, serial.repos[rid].provided)
        )


def test_a_short_read_never_starts_a_pool(eco, monkeypatch):  # noqa: F811
    a, state = eco
    _many(a)

    def boom(*a, **k):
        raise AssertionError("a pool started on a short budget")

    monkeypatch.setattr(pool, "ProcessPoolExecutor", boom)
    index.read(a, state, budget_s=1)  # a hook's budget


def test_a_chunk_not_back_by_the_deadline_counts_unread(monkeypatch):
    import time

    p = pool.Pool(True)
    monkeypatch.setattr(pool, "_extract_many", _slow)
    try:
        out = p.run([("a.py", "x"), ("b.py", "y")], time.monotonic() + 0.3)
    finally:
        p.close()
    assert out == [None, None]


def test_a_broken_pool_falls_back_to_in_process_extraction(eco, monkeypatch):  # noqa: F811
    a, state = eco
    _many(a)

    def broken_run(self, items, deadline, read=None):
        self.broken, self.enabled = True, False
        return [None] * len(items)

    monkeypatch.setattr(pool.Pool, "run", broken_run)
    e = index.read(a, state, budget_s=30)
    assert e.partial == {}
    assert any(f.key == "svc-7.acme.io" for f in e.repos["acme/svc-a"].mentioned)


def _slow(items):
    import time

    time.sleep(5)
    return [([], []) for _ in items]
