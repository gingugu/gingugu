"""What ``--migrate`` puts in ``.claude/hooks/retired/`` and how ``--prune`` trusts it.

The kit's file names, the sha256 manifest of every file moved, and the checks
that keep a planted manifest or a symlink from steering a move or a delete
outside ``retired/``. Shared by ``harness_migrate`` and ``harness_prune``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST = ".gingugu-manifest.json"

LEGACY_SCRIPTS = frozenset(
    f"{name}.py"
    for name in (
        "config_change",
        "cwd_changed",
        "elicitation",
        "elicitation_result",
        "file_changed",
        "instructions_loaded",
        "notification",
        "permission_request",
        "post_compact",
        "post_tool_use",
        "post_tool_use_failure",
        "session_end",
        "stop_failure",
        "subagent_start",
        "subagent_stop",
        "task_completed",
        "task_created",
        "teammate_idle",
        "user_prompt_submit",
    )
)
# The kit's helper package. Only these move; anything else in utils/ is the user's.
KIT_UTILS = (
    "__init__.py",
    "discover_endpoints.py",
    "llm/__init__.py",
    "llm/anth.py",
    "llm/task_summarizer.py",
    "tts/__init__.py",
    "tts/elevenlabs_tts.py",
    "tts/openai_tts.py",
    "tts/pyttsx3_tts.py",
    "tts/tts_queue.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_MANIFEST_KEYS = LEGACY_SCRIPTS | {f"utils/{rel}" for rel in KIT_UTILS}


def read_manifest(retired: Path) -> dict[str, str]:
    """The manifest's entries - only ever the kit's own file names.

    A planted manifest must not be able to name ``../../LICENSE`` or an absolute
    path, and a symlinked manifest is not trusted at all.
    """
    data = None
    if (retired / MANIFEST).is_file() and not (retired / MANIFEST).is_symlink():
        try:
            data = json.loads((retired / MANIFEST).read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            data = None
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in _MANIFEST_KEYS and isinstance(v, str)}


def no_symlink_between(root: Path, path: Path) -> bool:
    """True when no folder from ``root`` down to ``path``'s parent is a symlink."""
    parent = path.parent
    while parent != root and root in parent.parents:
        if parent.is_symlink():
            return False
        parent = parent.parent
    return not root.is_symlink()
