"""X7a: the env map - which files read an environment variable, and which set it.

Nothing imports an environment variable, so the code reading ``DB_URL`` and the
compose file, manifest, workflow, Dockerfile or ``.env.example`` that sets it are
invisible to each other in the import graph. ``scan_text`` (``env_scan``) finds
literal names in known forms; this module runs it over the checkout and caches
the result per file (the plumbing is ``file_facts``). Names and line numbers only:
no value is ever kept, and a real dotenv file is never opened.

Client side only: the brain is never asked. The cache is a hint, not a truth:
it is read as untrusted input, and a malformed entry is dropped and re-scanned.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from . import file_facts
from .env_scan import NAME, kind, scan_text, valid_name  # noqa: F401
from .file_facts import Refs, names_in  # noqa: F401

MAX_FILE_BYTES = 262_144  # a file past this is skipped entirely
MAX_NAMES = 200  # per file, reads and sets each


class EnvMap(NamedTuple):
    reads: Refs
    sets: Refs
    complete: bool = True  # False: the budget ran out before every file was read
    unread: tuple[str, ...] = ()  # too big, or cut at the name cap: 'nowhere' is unsafe


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_ENV", "on").lower() not in ("0", "off", "false")


def _spec() -> file_facts.Spec:
    # Built per call: a patched scan_text or cap is looked up here, not bound at import.
    return file_facts.Spec(
        ("reads", "sets"),
        "xray-env",
        lambda rel: kind(rel),
        lambda rel, text: scan_text(rel, text),
        valid_name,
        MAX_FILE_BYTES,
        MAX_NAMES,
    )


def _load(path: Path) -> tuple[dict[str, dict], object]:
    """(entries, raw) from the cache, every field type-checked."""
    return file_facts.load(path, _spec())


def read(root: Path, state_dir: Path, *, budget_s: float | None = None) -> EnvMap:
    """The env map for the checkout at ``root``, from the cache where it holds.
    With ``budget_s``, files not reached in time keep their cached entry, or wait
    for the next call; what was read is written either way, so the cache fills."""
    (reads, sets), complete, unread = file_facts.read(root, state_dir, _spec(), budget_s=budget_s)
    return EnvMap(reads, sets, complete, unread)
