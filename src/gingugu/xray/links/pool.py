"""X23: cold extraction on worker processes, for a read with the time to start them.

Measured on 48 checkouts: after generated code is skipped, a cold read is still
~20s of pure-Python scanning spread over every file type - CPU work threads
cannot overlap. A pool of processes can. Starting one costs ~0.5s, so it is used
only for a deliberate read (the ``links`` action, ``--warm``) with enough cold
files to pay for it, never inside a hook; and only with the default extractors,
which a worker can import (a test's own extractor runs in-process).

Files go to the pool in bounded batches (memory) and chunks (pickling), and a
chunk not back by the deadline is left: its files count as unread, as any file
past the budget does.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, wait

MIN_FILES = 64  # cold files in one repo below which in-process is faster
MIN_BUDGET_S = 10.0  # a read shorter than this never starts a pool
CHUNK = 32  # files per task
IN_FLIGHT = 4  # chunks per worker held at once (bounds memory)

Item = tuple[str, str]  # (path, text)
Result = tuple[list, list] | None


def _extract_many(items: list[Item]) -> list[Result]:
    """In a worker: the default extractors over a chunk (imported here, by name)."""
    from .index import _default_extract

    out: list[Result] = []
    for path, text in items:
        try:
            out.append(_default_extract(path, text))
        except Exception:  # noqa: BLE001 - one odd file never costs the chunk
            out.append(([], []))
    return out


class Pool:
    """One process pool for a whole read, shared by the sibling threads; started
    the first time a repo has enough cold files, shut down by ``close``."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.broken = False  # a worker died (a caller's unguarded __main__, an OOM kill)
        self._ex: ProcessPoolExecutor | None = None
        self.workers = max(1, min(8, (os.cpu_count() or 2) - 1))
        self.batch = self.workers * IN_FLIGHT * CHUNK  # files held at once, per caller

    def wants(self, n: int) -> bool:
        return self.enabled and n >= MIN_FILES

    def _executor(self) -> ProcessPoolExecutor:
        if self._ex is None:
            self._ex = ProcessPoolExecutor(max_workers=self.workers)
        return self._ex

    def run(
        self, items: list[Item], deadline: float, read: Callable[[Item], Item | None] | None = None
    ) -> list[Result]:
        """Results in item order; ``None`` for a file not done by the deadline."""
        ex = self._executor()
        results: list[Result] = [None] * len(items)
        batch = self.batch
        for start in range(0, len(items), batch):
            if time.monotonic() >= deadline:
                break
            futs: dict[Future, int] = {}
            for off in range(start, min(start + batch, len(items)), CHUNK):
                futs[ex.submit(_extract_many, items[off : off + CHUNK])] = off
            done, _late = wait(futs, timeout=max(0.0, deadline - time.monotonic()))
            for fut in done:
                off = futs[fut]
                try:
                    for i, r in enumerate(fut.result()):
                        results[off + i] = r
                except Exception:  # noqa: BLE001 - a broken pool: the caller runs it in-process
                    self.broken = True
            if self.broken or len(done) < len(futs):
                break
        if self.broken:
            self.enabled = False  # a pool that broke once is not tried again this read
        return results

    def close(self) -> None:
        if self._ex is not None:
            self._ex.shutdown(wait=False, cancel_futures=True)
            self._ex = None
