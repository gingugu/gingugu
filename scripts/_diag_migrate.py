"""TEMPORARY: time the test fixtures' on-disk migrate on this runner. Removed before merge."""

import os
import sqlite3
import tempfile
import time

from gingugu.migrations import migrate

d = tempfile.mkdtemp()
for label in ("disk", "disk", "memory", "memory+backup", "memory+backup"):
    t = time.perf_counter()
    path = os.path.join(d, f"{label}-{time.perf_counter_ns()}.db")
    if label == "disk":
        c = sqlite3.connect(path)
        migrate(c)
    elif label == "memory":
        c = sqlite3.connect(":memory:")
        migrate(c)
    else:
        m = sqlite3.connect(":memory:")
        migrate(m)
        c = sqlite3.connect(path)
        m.backup(c)
    c.close()
    print(f"{label:15s} {time.perf_counter() - t:7.3f}s", flush=True)
