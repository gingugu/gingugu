"""Payload shapes for memories on the read and write surfaces.

``_memory_summary`` is the full shape, ``_compact_summary`` the cheap one, and
``_summarizer`` picks between them for a read. Fields that are only meaningful
when set (``pinned``, ``about``, ``provenance``) are omitted otherwise, so an
ordinary memory pays nothing for them.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import decay
from ..models import Memory


def _age_field(mem: Memory) -> str | None:
    """Derived per read, never stored — see ``decay.age_label``. Ships even in
    full mode: the raw ISO timestamps are already there and still get misread,
    because the date arithmetic is done unreliably or skipped outright.

    Anchored on ``reference_timestamp``, not raw ``created_at``, so ``age``
    agrees with the three consumers (scorer, spread-neighbour sort, staleness)
    that were already using the freshness anchor.
    """
    anchor = decay.reference_timestamp(mem.last_confirmed, mem.updated_at, mem.created_at)
    return decay.age_label(mem.created_at, anchor)


def _memory_summary(mem: Memory) -> dict:
    data = {
        "id": mem.id,
        "type": mem.type.value,
        "title": mem.title,
        "content": mem.content,
        "confidence": mem.confidence.value,
        "namespace_id": mem.namespace_id,
        "created_at": mem.created_at,
        "last_confirmed": mem.last_confirmed,
        "access_count": mem.access_count,
        "tags": mem.tags,
    }
    # Only surfaced when set: an explicit false/null on every ordinary memory
    # would be noise on every single result.
    if mem.pinned:
        data["pinned"] = True
    if mem.about:
        data["about"] = mem.about
    _add_provenance(data, mem)
    age = _age_field(mem)
    if age is not None:
        data["age"] = age
    if mem.score is not None:
        data["score"] = round(mem.score, 4)
    return data


# Compact summaries replace full content with a short excerpt. 200 chars is
# roughly two terminal lines — enough to recognize the memory and decide
# whether to pull the full body via memory_recall.
_COMPACT_CONTENT_CHARS = 200


def _compact_summary(mem: Memory) -> dict:
    """Lightweight variant of ``_memory_summary`` for ``compact`` reads
    (memory_context, memory_recall, memory_search) and for the write-time
    hints (``hints.find_similar``, ``hints.suggest_relations``), which are always
    compact regardless of any caller flag — they are unasked-for extras on a
    write, so they must stay cheap.

    Full ``content`` is replaced by a whitespace-normalized excerpt under
    ``summary``; bookkeeping fields (raw timestamps, access_count) are dropped.
    ``namespace_id`` is identity, not bookkeeping — kept so namespace
    stamping works uniformly across full and compact payloads. ``age`` is kept
    too: the protocol mandates compact at session start, so dropping every
    temporal signal left the agent time-blind exactly when reading the RESUME
    memory — unable to tell last night's note from June's. It costs ~4 tokens.
    """
    excerpt = " ".join(mem.content.split())
    if len(excerpt) > _COMPACT_CONTENT_CHARS:
        excerpt = excerpt[:_COMPACT_CONTENT_CHARS].rsplit(" ", 1)[0] + " …"
    data = {
        "id": mem.id,
        "type": mem.type.value,
        "title": mem.title,
        "summary": excerpt,
        "confidence": mem.confidence.value,
        "namespace_id": mem.namespace_id,
        "tags": mem.tags,
    }
    _add_provenance(data, mem)
    age = _age_field(mem)
    if age is not None:
        data["age"] = age
    if mem.score is not None:
        data["score"] = round(mem.score, 4)
    return data


def _add_provenance(data: dict, mem: Memory) -> None:
    """Compact too: a self-concluded claim must arrive visibly contestable."""
    if mem.provenance:
        data["provenance"] = mem.provenance.value


def _summarizer(compact: bool = False, explain: bool = False) -> Callable[[Memory], dict]:
    """Pick the payload shape for a read surface.

    ``explain`` adds ``score_breakdown`` - the weighted terms ``score`` is the
    sum of. Opt-in rather than always-on because it is a diagnostic, and every
    always-on field is paid for on every hit of every read: the question "why
    did this rank here?" is asked rarely and deliberately.

    A memory with no ``score_parts`` simply carries no breakdown. That is not a
    gap to paper over - pins never entered the ranking, and a bare relevance or
    a column-ordered listing has no composite behind it, so there is genuinely
    nothing to decompose.
    """
    base = _compact_summary if compact else _memory_summary
    if not explain:
        return base

    def explained(mem: Memory) -> dict:
        data = base(mem)
        if mem.score_parts:
            data["score_breakdown"] = {k: round(v, 4) for k, v in mem.score_parts.items()}
        return data

    return explained
