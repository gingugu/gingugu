"""X23: walking other people's checkouts safely - the path check and the worker pool.

``Inside`` answers "is this listed name a regular file inside the checkout, with
no link anywhere on its way there", cheaply enough for thousands of names.
``gather`` runs one job per sibling on daemon threads and stops waiting at the
deadline plus a grace period, so a hung sibling can never hold a hook open.
"""

from __future__ import annotations

import os
import queue
import stat
import threading
import time
from pathlib import Path, PurePosixPath

MAX_STR = 1000
WORKERS = 8


class Inside:
    """``root / name`` and its stat when it is a regular file inside ``root``: no
    absolute name, no ``..``, and no link at the end or on the way. Each directory
    is checked once (``lstat``), not each file resolved: a crafted index can list
    any name, and a big repo lists thousands."""

    MAX_DEPTH = 64  # path parts; past it a name is crafted, not a repo layout
    MAX_DIRS = 20_000  # cached directory answers; a crafted index cannot grow it past this

    def __init__(self, root: Path):
        self.root = root
        self.dirs: dict[str, bool] = {"": True}

    def _dir_ok(self, rel: str) -> bool:
        if len(self.dirs) > self.MAX_DIRS:
            self.dirs = {"": True}
        todo = []
        while rel not in self.dirs:
            todo.append(rel)
            rel = rel.rpartition("/")[0]
        ok = self.dirs[rel]
        for d in reversed(todo):
            if ok:
                try:
                    ok = stat.S_ISDIR((self.root / d).lstat().st_mode)
                except OSError:
                    ok = False
            self.dirs[d] = ok
        return ok

    def __call__(self, name: str) -> tuple[Path, os.stat_result] | None:
        pure = PurePosixPath(name)
        if not name or pure.is_absolute() or os.path.isabs(name):
            return None
        parts = name.split("/")
        if (
            len(parts) > self.MAX_DEPTH
            or len(name) > MAX_STR
            or any(p in ("..", ".") for p in parts)
        ):
            return None
        if os.name == "nt" and ("\\" in name or ":" in name):
            return None  # a separator or drive there, so the ".." check above would miss it
        if not self._dir_ok(name.rpartition("/")[0]):
            return None
        full = self.root / name
        try:
            st = full.lstat()
        except OSError:
            return None
        return (full, st) if stat.S_ISREG(st.st_mode) else None


LATE = object()
GRACE_S = 2.0  # how long past the budget a worker still finishing is waited for


def gather(items: list, work, deadline: float) -> list:
    """``work(item)`` for each item on ``WORKERS`` daemon threads, results in item
    order. A worker still running ``GRACE_S`` past the deadline is left behind
    (its slot reads ``LATE``): a daemon thread never holds the process open, so a
    hung sibling cannot keep a hook from exiting."""
    results: list = [LATE] * len(items)
    todo: queue.SimpleQueue = queue.SimpleQueue()
    for i in range(len(items)):
        todo.put(i)
    done = threading.Semaphore(0)

    def run() -> None:
        while True:
            try:
                i = todo.get_nowait()
            except queue.Empty:
                return
            try:
                results[i] = work(items[i])
            except Exception:  # noqa: BLE001
                results[i] = None
            done.release()

    for _ in range(min(WORKERS, len(items))):
        threading.Thread(target=run, daemon=True).start()
    for _ in items:
        if not done.acquire(timeout=max(0.0, deadline + GRACE_S - time.monotonic())):
            break
    return list(results)
