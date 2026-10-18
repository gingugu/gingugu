"""Whether Claude Code trusts a folder, read from ``~/.claude.json``.

Claude Code ignores the inline ``mcpServers`` of a project agent file until the
folder is trusted, so a minion installed by ``gingugu harness`` starts without
its ``brain`` server and nothing says why. The key read here is the one the
CLI itself names when it refuses (``projects[<dir>].hasTrustDialogAccepted``).
A trusted parent folder counts. Advisory only: used to warn, never to decide.
"""

from __future__ import annotations

import json
from pathlib import Path


def config_path() -> Path:
    return Path.home() / ".claude.json"


def trusted(folder: Path, config: Path | None = None) -> bool | None:
    """True or False from Claude Code's config; None when it cannot be read."""
    try:
        data = json.loads((config or config_path()).read_text())
    except (OSError, ValueError, RuntimeError):  # RuntimeError: no home directory
        return None
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return None
    for path in (folder, *folder.parents):
        # Native and forward-slash spellings: on Windows they differ.
        for key in {str(path), path.as_posix()}:
            entry = projects.get(key)
            if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
                return True
    return False


def untrusted_note(folder: Path) -> str | None:
    """The warning for a folder Claude Code is known not to trust, else None."""
    if trusted(folder) is not False:
        return None
    return (
        f"Claude Code has not trusted {folder}, so the inline `brain` server in this "
        "repo's agent files will not load and minions start without memory. Open "
        "Claude Code here once and accept the trust dialog."
    )
