"""Write a secret to an owner-only file, so the value never enters a tool response.

The caller names the path; this module decides whether it is safe to write
there. Everything that can be refused is refused BEFORE the secret is read, and
the write itself does not follow a symlink even if one appears after the checks.
"""

from __future__ import annotations

import os
from pathlib import Path

FILE_MODE = 0o600


def check_target(path: str) -> Path:
    """Validate a caller-supplied path and return it, expanded. Raises ValueError."""
    expanded = os.path.expanduser(path) if path else ""
    if not expanded or not os.path.isabs(expanded):
        raise ValueError(f"into must be an absolute path, got {path!r}")
    target = Path(expanded)
    if target.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {target}")
    if target.is_dir():
        raise ValueError(f"into names a directory, not a file: {target}")
    if not target.parent.is_dir():
        raise ValueError(f"parent directory does not exist: {target.parent}")
    return target


def write_private(target: Path, value: str) -> int:
    """Write ``value`` to ``target`` with mode 0600 and return the byte count.

    ``O_NOFOLLOW`` closes the gap between ``check_target`` and the open on POSIX,
    for the final component only; ancestors resolve as for any other path the
    caller names (``/tmp`` itself is a symlink on macOS). An existing file is
    truncated and its mode tightened BEFORE the write: ``os.open`` applies the
    mode only when it creates the file, and a pre-planted file owned by someone
    else fails ``fchmod`` with the secret still unwritten.

    Windows has no ``O_NOFOLLOW`` and ``chmod`` there only toggles read-only, so
    the file inherits its directory's ACL and a link planted between the check
    and the open is followed.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    data = value.encode("utf-8")
    fd = os.open(target, flags, FILE_MODE)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, FILE_MODE)
        else:  # pragma: no cover - Windows
            os.chmod(target, FILE_MODE)
        os.write(fd, data)
    finally:
        os.close(fd)
    return len(data)
