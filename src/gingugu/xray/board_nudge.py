"""X20, the sender's half: ask a handoff from a branch to name its range, once.

A receiver renders a brief only for a post that names a range (``board_brief``),
and nothing reminds the writer to. So ``gingugu hook tool`` stops the first
board post of a session that names none, when the sender's checkout is on a
branch ahead of the default branch, with the exact line to add as the reason.
The re-issued post passes, so a post that is not about the branch costs one
retry per session. It runs on the sender's machine and reads only its checkout.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import board_brief, hook_common, local, repo_id, scan
from .hook_common import _show
from .scan import XrayError

_BOARD = re.compile(r"mcp__.+__board")
_DEFAULTS = ("main", "master")
# Each call is a local ref lookup; the hook as a whole has 10-15s.
_GIT_TIMEOUT_S = 3


def is_post(tool_name: str, tool_input: object) -> bool:
    """A board ``post`` call - cheap, so every tool call can ask."""
    return (
        bool(_BOARD.fullmatch(tool_name or ""))
        and isinstance(tool_input, dict)
        and tool_input.get("action") == "post"
    )


def _out(repo: Path, *args: str) -> str | None:
    proc = scan.git(repo, *args, timeout=_GIT_TIMEOUT_S)
    return proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else None


def _base(repo: Path) -> str | None:
    """The default branch: origin's HEAD when it is known locally, else main, else master."""
    origin = _out(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD") or ""
    found = [origin.removeprefix("origin/")] if origin.startswith("origin/") else []
    for name in [*found, *_DEFAULTS]:
        if _out(repo, "rev-parse", "--verify", "-q", f"refs/heads/{name}") is not None:
            return name
    return None


def _ahead(repo: Path) -> tuple[str, str, int] | None:
    """(base, branch, commits ahead) when HEAD is a branch ahead of the default."""
    branch = _out(repo, "symbolic-ref", "-q", "--short", "HEAD")
    base = _base(repo)
    if not branch or not base or branch == base:
        return None
    count = _out(repo, "rev-list", "--count", f"{base}..{branch}")
    if not count or not count.isdigit() or int(count) == 0:
        return None
    return base, branch, int(count)


def check(payload: dict, state_dir: Path) -> str | None:
    """The deny reason for this board post, or None to let it through."""
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id")
    if not board_brief.enabled() or not is_post(payload.get("tool_name") or "", tool_input):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None  # a shared "unknown" would let one session silence another
    if board_brief.ref_in(tool_input.get("body")) is not None:
        return None
    cwd = payload.get("cwd")
    try:
        repo = local.root(cwd if isinstance(cwd, str) and cwd else None)
        found = _ahead(repo)
    except (XrayError, ValueError, OSError):
        return None
    if found is None:
        return None
    base, branch, n = found
    claim = hook_common.claim_path(state_dir, "xray-board-nudge", session_id, repo)
    if not hook_common._claim(claim):
        return None  # already asked this session
    name = repo_id.of(repo)
    line = f"xray: {_show(name) + ' ' if name else ''}{_show(base)}..{_show(branch)}"
    commits = "commit" if n == 1 else "commits"
    return (
        f"GINGUGU X-RAY: this board post comes from {_show(branch)}, {n} {commits} ahead "
        f"of {_show(base)}. If it hands off that work, add a line of its own:\n"
        f"  {line}\n"
        "and the receiver gets the X-ray brief of the change, rendered from its own "
        "checkout. Not about this branch? Re-issue the post unchanged - this asks once "
        "per session."
    )
