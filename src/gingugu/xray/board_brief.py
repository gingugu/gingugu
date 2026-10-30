"""X20: a board post that names a range arrives with its X-ray brief.

The agents X19's brief is for fire no hooks, so a handoff should carry its brief
unasked. The post carries only the ref (a line of its own, ``xray: <range>`` or
``xray: <repo> <range>``); the brief is rendered on the receiver, from its own
checkout of that repo (``repo_id``), so no paths or stale text cross the board.
The ref is another agent's words: it passes the same gate as any range before
git sees it, and only a shell-safe range enters a suggested command.
"""

from __future__ import annotations

import json
import os
import re

from . import brief, local, repo_id
from .scan import XrayError

MAX_REF = 200
_MARKER = re.compile(r"^[ \t]*xray:", re.IGNORECASE | re.MULTILINE)
_LINE = re.compile(r"^[ \t]*xray:[ \t]*(\S+(?:[ \t]+\S+)*)[ \t]*$", re.IGNORECASE)


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_BOARD", "on").lower() not in ("0", "off", "false")


def parse(body: object) -> tuple[str | None, str] | None:
    """(repo or None, range) from the first ``xray:`` line, or None."""
    if not isinstance(body, str):
        return None
    found = _MARKER.search(body)
    if found is None:
        return None
    line = body[found.start() :].split("\n", 1)[0].rstrip("\r")
    m = _LINE.match(line)
    if m is None:
        return None
    tokens = m.group(1).split()
    if len(tokens) not in (1, 2):
        return None
    repo = tokens[0] if len(tokens) == 2 else None
    ref = tokens[-1]
    if len(ref) > MAX_REF or (repo is not None and not repo_id.valid(repo)):
        return None
    try:
        brief._check_range(ref)
    except ValueError:
        return None
    return repo, ref


def ref_in(body: object) -> str | None:
    """The range a post names, or None. The first ``xray:`` line decides."""
    parsed = parse(body)
    return parsed[1] if parsed else None


CLIPPED = "(brief clipped to fit; code_xray brief or `gingugu xray brief` has all of it)"


def _fit(text: str, max_chars: int) -> str:
    """``text`` within ``max_chars``, cut on a line; a cut says so on its last line."""
    if len(text) <= max_chars:
        return text
    budget = max_chars - len(CLIPPED) - 1
    if budget <= 0:
        return text[:max_chars]
    kept: list[str] = []
    used = 0
    for line in text.split("\n"):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    body = "\n".join(kept) if kept else text[:budget]
    return body + "\n" + CLIPPED


# The range gate keeps git safe, not a shell: only these reach a suggested command.
_SHELL_SAFE = re.compile(r"[A-Za-z0-9._/~^@{}:+=-]+")


def _not_here(ref: str, repo: str | None = None) -> str:
    """One line for a receiver with no checkout: a silent None would drop the ref."""
    where = f"no checkout of {repo} found" if repo else "no git checkout"
    arg = ref if _SHELL_SAFE.fullmatch(ref) else "<range>"
    return (
        f"X-ray brief: {json.dumps(ref)} is not available here ({where}); "
        f"run `gingugu xray brief --range {arg}` in that repo."
    )


def attach(
    body: object, cwd: str | None, max_chars: int, session_id: str | None = None
) -> str | None:
    """The brief for the range ``body`` names, rendered from ``cwd``; None when
    there is nothing to say. Never raises."""
    try:
        parsed = parse(body)
        if not enabled() or parsed is None:
            return None
        repo, ref = parsed
        if repo is not None:
            found = repo_id.find(repo, cwd)
            if found is None:
                return _not_here(ref, repo)
            cwd = str(found)
        elif not cwd:
            return _not_here(ref)
        else:
            try:
                local.root(cwd)
            except (XrayError, ValueError, OSError):
                return _not_here(ref)  # a watcher outside a repo, e.g. a Codex thread dir
        try:
            data = brief.answer(cwd, ref, None, session_id=session_id)
        except (XrayError, ValueError) as exc:
            why = json.dumps(str(exc)[:120])
            return f"X-ray brief: {json.dumps(ref)} is not available from this checkout ({why})."
        label = f"{repo} {ref}" if repo else ref
        head = f"X-ray brief for {json.dumps(label)}, rendered on this machine from this checkout:"
        return _fit(head + "\n" + data["text"], max_chars)
    except Exception:  # noqa: BLE001 - a brief must never cost the mail
        return None
