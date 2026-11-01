"""X3 scar tissue: what a file has cost before, from the memories that name it.

No code tool has this, because none holds the history. Derived at read time,
never stored: the brain FTS-searches this repo's project namespace for the
file's basename and sends back each hit's path-like tokens (``scar_query``);
here they are resolved against the checkout. A memory is about this file when
one of its tokens is this file - the full path, or a suffix no other file
shares. It scores its type's weight over how many repo files it names: a bug
about one file is a scar, a plan naming thirty mentions it in passing.
Recency breaks ties.

Only the repo's own namespace is asked: in ``crow`` a file name is usually
another repo's file of the same name. Like every hook helper it never raises:
no brain, a refused grant or a failed query is no scars.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from . import scan

TOP_N = 3
_TITLE_SHOWN = 120
# What a memory of each type costs: a bug is a scar; a decision or the shape of
# the code is why it is the way it is; a fact or pattern is a note about it.
WEIGHTS = {"bug": 3.0, "decision": 2.0, "architecture": 2.0, "fact": 1.0, "pattern": 1.0}


class Scar(NamedTuple):
    id: str
    type: str
    title: str
    created_at: str
    named: int  # repo files the memory names, this one included
    score: float


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_SCARS", "on").lower() not in ("0", "off", "false")


def project_namespace(root: Path) -> str:
    """The checkout's project namespace: its folder name, or for a worktree
    its main checkout's - a worktree's own folder names a branch, not a repo."""
    try:
        proc = scan.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        common = Path(proc.stdout.decode().strip()) if proc.returncode == 0 else None
    except (OSError, ValueError, scan.XrayError):
        common = None
    if common is not None and common.name == ".git":
        return common.parent.name
    return root.name


def _resolve(token: str, files: set[str]) -> str | None:
    """The one repo file ``token`` names, ``""`` when several share it, None
    when it names none: the full path, else a suffix ending on a ``/``."""
    token = token.removeprefix("./")
    if token in files:
        return token
    tail = "/" + token
    found = [f for f in files if f.endswith(tail)]
    if len(found) == 1:
        return found[0]
    return "" if found else None


def rank(hits: list[dict], rel: str, files: set[str]) -> list[Scar]:
    """The hits about ``rel``, best first."""
    out = []
    for hit in hits:
        weight = WEIGHTS.get(hit.get("type"))
        if weight is None:
            continue
        resolved = {_resolve(t, files) for t in hit.get("paths", ())}
        if rel not in resolved:
            continue
        # An ambiguous name still names some file: it dilutes, it never matches.
        named = len(resolved - {None, ""}) + (1 if "" in resolved else 0)
        out.append(
            Scar(hit["id"], hit["type"], hit["title"], hit["created_at"], named, weight / named)
        )
    out.sort(key=lambda s: s.id)
    out.sort(key=lambda s: s.created_at, reverse=True)
    out.sort(key=lambda s: s.score, reverse=True)  # stable: score, then newest, then id
    return out


def _flat(text: str) -> str:
    """One line, printable: a title is the user's text and must not start a
    line of its own in the agent's context."""
    flat = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return flat if len(flat) <= _TITLE_SHOWN else flat[: _TITLE_SHOWN - 3] + "..."


def lines(found: list[Scar]) -> list[str]:
    """The lines a hook appends: a count, then the top ``TOP_N``."""
    if not found:
        return []
    n = len(found)
    out = [f"Scar tissue: {n} memor{'y names' if n == 1 else 'ies name'} this file:"]
    for s in found[:TOP_N]:
        out.append(f"- {s.type} {s.created_at[:10]}: {_flat(s.title)} [{s.id}]")
    if n > TOP_N:
        out.append(f"(+{n - TOP_N} more; memory_search ids=<id> reads one in full)")
    return out


def fetch(root: Path, rel: str) -> list[dict]:
    """The brain's hits for ``rel``'s basename in this repo's namespace."""
    if not enabled():
        return []
    basename = rel.rsplit("/", 1)[-1]
    if basename == "__init__.py":
        return []  # every package has one; the name says nothing
    namespace = project_namespace(root)
    from .. import hook_grant, hook_remote

    try:
        grant = hook_grant.client_grant()
    except ValueError:
        return []  # a grant that cannot be honoured reads nothing
    if grant is not None and not grant.can_read(namespace):
        return []
    target = hook_remote.target()
    if target is not None:
        from ..hook_remote_flows import remote_scars

        return remote_scars(target, namespace, basename) or []
    from ..config import load_config
    from ..recall_sweep import connect_readonly
    from .scar_query import scars

    db_path = load_config().db_path
    if not db_path.exists():
        return []
    conn = connect_readonly(db_path)
    try:
        return scars(conn, namespace, basename)
    finally:
        conn.close()


def for_file(root: Path, rel: str, files: set[str]) -> list[str]:
    """Scar lines for ``rel`` against the checkout's ``files``; [] on any failure."""
    try:
        return lines(rank(fetch(root, rel), rel, files))
    except Exception:  # noqa: BLE001 - scars never cost a read or an edit its X-ray
        return []
