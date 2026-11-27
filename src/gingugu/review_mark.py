"""Reviewed-as-history mark: a deliberate, content-bound note in ``metadata.review``.

A reviewer who read a memory and judged it accurate history (a narrated wait, a
ref the claims ledger cannot hold) records ``{"review": {"history": <date>,
"by": <who>, "sha": <content hash>}}``. The hash binds the mark to the content
it was set on, so any content edit voids it; a hand-written block without a
matching hash is not a mark. Pure functions, no DB.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

REVIEW_VALUES = ("history",)


def content_sha(content: str) -> str:
    """First 16 hex chars of the SHA-256 of the UTF-8 content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _as_object(metadata: str | dict | None) -> dict | None:
    if isinstance(metadata, dict):
        return metadata
    if not isinstance(metadata, str) or not metadata.strip():
        return None
    try:
        parsed = json.loads(metadata)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def history_mark(metadata: str | dict | None, content: str) -> dict | None:
    """The valid history mark for this content, or None."""
    obj = _as_object(metadata)
    block = obj.get("review") if obj else None
    if not isinstance(block, dict):
        return None
    day, by, sha = block.get("history"), block.get("by"), block.get("sha")
    if not (isinstance(day, str) and isinstance(by, str) and isinstance(sha, str)):
        return None
    if sha != content_sha(content):
        return None
    return {"as": "history", "on": day, "by": by}


def with_history_mark(metadata: str | dict | None, content: str, author: str) -> str:
    """Metadata JSON with a fresh mark for ``content`` merged in; other keys kept."""
    obj = dict(_as_object(metadata) or {})
    obj["review"] = {
        "history": datetime.now(UTC).date().isoformat(),
        "by": author,
        "sha": content_sha(content),
    }
    return json.dumps(obj)


def without_review(metadata: str | dict | None) -> str | None:
    """Metadata JSON with ``review`` removed; ``""`` (clear) when nothing is left,
    None when there was nothing to remove."""
    obj = _as_object(metadata)
    if not obj or "review" not in obj:
        return None
    rest = {k: v for k, v in obj.items() if k != "review"}
    return json.dumps(rest) if rest else ""
