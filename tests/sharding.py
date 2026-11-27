"""Split the suite into N shards by test file, for a CI cell too slow to run whole.

``GINGUGU_TEST_SHARD=2/3`` keeps the files whose shard is 2 of 3 and deselects
the rest. Unset, nothing is filtered. A whole file stays in one shard, so its
module fixtures are built once. The shard comes from a CRC of the file's path,
so it is stable across runs, machines and xdist workers (every worker collects
the same list and must agree on it).

Why: the windows-latest runner varies about 2x between runs of the same commit
(pytest measured at 311s, 326s and 581s), so one cell of the whole suite cannot
stay under its 10-minute cap. Two shards keep each under 5 minutes even on a
slow runner, without raising the cap.
"""

from __future__ import annotations

import os
import zlib

ENV = "GINGUGU_TEST_SHARD"


def parse(spec: str) -> tuple[int, int]:
    """``"2/3"`` -> ``(2, 3)``; anything else is a usage error, never a silent full run."""
    try:
        index, total = (int(part) for part in spec.split("/"))
    except ValueError:
        raise ValueError(f"{ENV} must look like 1/2, got {spec!r}") from None
    if not 1 <= index <= total:
        raise ValueError(f"{ENV} must look like 1/2, got {spec!r}")
    return index, total


def shard_of(nodeid: str, total: int) -> int:
    """1-based shard of the test file ``nodeid`` belongs to."""
    path = nodeid.split("::", 1)[0]
    return zlib.crc32(path.encode()) % total + 1


def pytest_collection_modifyitems(config, items) -> None:
    spec = os.environ.get(ENV, "").strip()
    if not spec:
        return
    index, total = parse(spec)
    keep, drop = [], []
    for item in items:
        (keep if shard_of(item.nodeid, total) == index else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
