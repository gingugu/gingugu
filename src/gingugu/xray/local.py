"""The client side of X-ray: the only code that touches the caller's disk.

Shared by the local stdio server and the proxy, so both answer the same way:
take a path (default: the client's working directory), fingerprint or extract
the git checkout it sits in, and turn the files an agent names - often
absolute paths - into the repo-relative paths a snapshot uses.

The brain never runs this. Under `gingugu serve` a path would be the host's
disk, so the tool there accepts only a fingerprint or a payload.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .scan import toplevel


def root(path: str | None) -> Path:
    """The git toplevel that ``path`` (or the working directory) belongs to."""
    return toplevel(path or os.getcwd())


def file_list(files) -> list[str]:
    """A comma-separated string or a list, as the tool accepts either."""
    if files is None:
        return []
    if isinstance(files, str):
        files = files.split(",")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise ValueError("files must be a comma-separated string or a list of paths")
    return [f.strip() for f in files if f.strip()]


def seeds(files: list[str], repo: Path) -> list[str]:
    """Repo-relative POSIX paths. A file outside the checkout - an absolute path
    elsewhere, or one climbing out with ``..`` - is refused here, so a path from
    this machine never travels to the brain."""
    out = []
    for f in files:
        p = Path(f)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(repo.resolve())
            except ValueError:
                raise ValueError(f"{f} is outside the checkout at {repo}") from None
        parts = PurePosixPath(*p.parts).parts
        if ".." in parts:
            raise ValueError(f"{f} is outside the checkout at {repo}")
        out.append("/".join(part for part in parts if part != "."))
    return out
