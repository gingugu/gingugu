"""The project a working directory belongs to, as the hooks name its namespace.

The folder name, except in a linked git worktree: there the folder names a
branch (``.claude/worktrees/fix-y``), so the name is the main checkout's. A
worktree's ``.git`` is a file, ``gitdir: <main>/.git/worktrees/<name>``; it is
read, never git spawned, because the prompt and tool hooks run on every prompt
and every tool call. A submodule's ``.git`` points into ``.git/modules`` and
keeps its own name. Any surprise falls back to the folder name.

The SessionStart template (``bootstrap/templates/session_start.py.tmpl``) is
pure stdlib and carries its own copy of this; keep the two in step.
"""

from __future__ import annotations

from pathlib import Path


def project_name(cwd: str) -> str:
    here = Path(cwd)
    try:
        text = (here / ".git").read_text(errors="replace").strip()
    except (OSError, ValueError):
        return here.name  # no .git file: a main checkout, or no repo at all
    if not text.startswith("gitdir:"):
        return here.name
    try:
        gitdir = Path(text[len("gitdir:") :].strip())
        if not gitdir.is_absolute():
            gitdir = here / gitdir
    except (OSError, ValueError):
        return here.name
    parts = [p for p in gitdir.as_posix().split("/") if p not in ("", ".")]
    # <main>/.git/worktrees/<name>, ``..`` folded lexically: no resolve(), so a
    # symlink on the way cannot move the answer.
    folded: list[str] = []
    for p in parts:
        if p == ".." and folded:
            folded.pop()
        elif p != "..":
            folded.append(p)
    if len(folded) >= 4 and folded[-2] == "worktrees" and folded[-3] == ".git":
        return folded[-4]
    return here.name
