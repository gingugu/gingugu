"""X7c: the table map - which files define a database table, drop it, and use it.

Nothing imports a table: the code that queries a table and the migration that
creates it are invisible to each other in the import graph.
``scan_text`` (``table_scan``) finds table names in known forms; this module runs
it over the checkout and caches the result per file (the plumbing is
``file_facts``). Names and line numbers only.

Client side only: the brain is never asked. The cache is a hint, not a truth:
it is read as untrusted input, and a malformed entry is dropped and re-scanned.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from . import file_facts
from .file_facts import Refs, names_in  # noqa: F401
from .table_scan import kind, scan_text, valid_name  # noqa: F401

MAX_FILE_BYTES = 262_144  # a file past this is skipped entirely
MAX_NAMES = 200  # per file, defines, drops and uses each


class TableMap(NamedTuple):
    defines: Refs
    drops: Refs
    uses: Refs
    complete: bool = True  # False: the budget ran out before every file was read


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_TABLES", "on").lower() not in ("0", "off", "false")


def _spec() -> file_facts.Spec:
    # Built per call: a patched scan_text or cap is looked up here, not bound at import.
    return file_facts.Spec(
        ("defines", "drops", "uses"),
        "xray-tables",
        lambda rel: kind(rel),
        lambda rel, text: scan_text(rel, text),
        valid_name,
        MAX_FILE_BYTES,
        MAX_NAMES,
    )


def _load(path: Path) -> tuple[dict[str, dict], object]:
    """(entries, raw) from the cache, every field type-checked."""
    return file_facts.load(path, _spec())


def dropped(m: TableMap, name: str) -> list[tuple[str, int]]:
    """Where ``name`` is dropped, in files that do not also define it: a drop and
    a recreate in one file is a rebuild, not a drop. A test dropping a table to
    simulate a failure says nothing about the schema."""
    from .env_hook import _is_test

    defining = {f for f, _ in m.defines.get(name, [])}
    return [(f, n) for f, n in m.drops.get(name, []) if f not in defining and not _is_test(f)]


def read(root: Path, state_dir: Path, *, budget_s: float | None = None) -> TableMap:
    """The table map for the checkout at ``root``, from the cache where it holds.
    With ``budget_s``, files not reached in time keep their cached entry, or wait
    for the next call; what was read is written either way, so the cache fills."""
    (defines, drops, uses), complete = file_facts.read(root, state_dir, _spec(), budget_s=budget_s)
    return TableMap(defines, drops, uses, complete)
