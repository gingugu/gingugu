"""Promotion's two halves that live in a brain: the net, and the receiving end.

``hold_reason`` is the locked filter from ``promote`` with its reason spoken:
the caller picks the ids, and the net says why any of them must stay home.

``receive`` is what a brain does with a promoted memory. A copy is matched to
its source by the source's id AND the contributor - and the contributor is
whoever the receiving server says is calling, never what the stamp claims, so
one crew mate cannot supersede another's promotion by forging a stamp. The
match happens here, in the receiving brain, because only it can read the
stamps: search never returns ``metadata``.

- no live copy of that source from that contributor: store it ("promoted")
- a live copy with the same title and content: nothing to do ("unchanged")
- a live copy that differs: store the new one, link it ``supersedes`` the old,
  and deprecate the old ("updated") - an edit replaces, it never piles up
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Confidence, MemoryType, Provenance, RelationType
from .promote import EPISODIC_TAGS, PERSONAL_TAGS, SECRET_TAGS, contains_secret
from .transactions import atomic

CREW = "crew"
PROMOTED_TAG = "promoted"


def hold_reason(
    *,
    confidence: str,
    tags: list[str],
    title: str,
    content: str,
    held: frozenset[str],
    about: str | None = None,
) -> str | None:
    """Why this memory must stay home, or None to let it go up. Every text
    field that travels is scanned - ``about`` is searchable in the commons."""
    if confidence != Confidence.VERIFIED.value:
        return f"not verified ({confidence})"
    lowered = {t.lower() for t in tags}
    for label, group in (
        ("session tag", EPISODIC_TAGS),
        ("personal tag", PERSONAL_TAGS | held),
        ("secret tag", SECRET_TAGS),
    ):
        hit = sorted(lowered & group)
        if hit:
            return f"{label}: {', '.join(hit)}"
    if any(contains_secret(text) for text in (title, content, about or "")):
        return "looks like a secret"
    return None


def stamp(*, source_id: str, namespace: str, contributor: str) -> dict:
    """The provenance stamp a promoted copy carries (see ``promote.provenance``)."""
    return {
        "promoted_from": {
            "instance": contributor,
            "namespace": namespace,
            "id": source_id,
            "contributor": contributor,
            "promoted_at": datetime.now(UTC).isoformat(),
        }
    }


def source_of(metadata: str | dict | None) -> dict | None:
    """The ``promoted_from`` stamp in a metadata payload, or None."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    if not isinstance(metadata, dict):
        return None
    found = metadata.get("promoted_from")
    if isinstance(found, dict) and isinstance(found.get("id"), str) and found["id"]:
        return found
    return None


@dataclass
class Promotion:
    """What a memory to promote carries into the receiving brain."""

    title: str
    content: str
    type: MemoryType
    tags: list[str]
    about: str | None
    provenance: Provenance | None
    stamp: dict  # the copy's whole metadata: the source's, plus ``promoted_from``


def _live_copies(conn, namespace_id: str, source_id: str, contributor: str) -> list:
    return conn.execute(
        "SELECT id, title, content FROM memories WHERE namespace_id = ? "
        "AND confidence != 'deprecated' "
        "AND json_extract(metadata, '$.promoted_from.id') = ? "
        "AND json_extract(metadata, '$.promoted_from.contributor') = ? "
        "ORDER BY created_at",
        (namespace_id, source_id, contributor),
    ).fetchall()


def receive(conn, store, relations, *, namespace_id: str, item: Promotion) -> dict:
    """Land one promoted memory in ``namespace_id``; see the module docstring.

    The stamp's contributor must already be the server-derived caller.
    """
    src = item.stamp["promoted_from"]
    copies = _live_copies(conn, namespace_id, src["id"], src["contributor"])
    for row in copies:
        if row["title"] == item.title and row["content"] == item.content:
            return {"status": "unchanged", "id": row["id"], "superseded": []}
    tags = list(dict.fromkeys([*item.tags, PROMOTED_TAG]))
    # One transaction: a failure between the new copy and the old one's
    # deprecation must not leave two live copies of one source.
    with atomic(store, relations):
        mem, superseded = _land(store, relations, namespace_id, item, src, tags, copies)
    return {
        "status": "updated" if superseded else "promoted",
        "id": mem.id,
        "superseded": superseded,
        "memory": mem,
    }


def _land(store, relations, namespace_id, item, src, tags, copies):
    mem = store.create(
        namespace_id=namespace_id,
        type=item.type,
        title=item.title,
        content=item.content,
        confidence=Confidence.VERIFIED,
        source=f"promotion:{src.get('namespace')}",
        metadata=json.dumps(item.stamp),
        tags=tags,
        provenance=item.provenance,
        about=item.about,
    )
    superseded = []
    for row in copies:
        relations.relate(
            source_id=mem.id, target_id=row["id"], relation_type=RelationType.SUPERSEDES
        )
        store.update(row["id"], confidence=Confidence.DEPRECATED)
        superseded.append(row["id"])
    return mem, superseded
