"""``memory_update(review=...)``: resolve the metadata a review mark implies."""

from __future__ import annotations

from .. import review_mark
from . import ServerContext
from .board import author_for
from .fence import request_grant


def review_metadata(
    ctx: ServerContext,
    existing,
    review: str | None,
    content: str | None,
    coerced: str | None,
) -> str | None:
    """Metadata to write for a ``review`` request, merged over ``coerced`` (the
    caller's own metadata, if any) or the memory's stored metadata. Raises
    ValueError for an unknown value; returns ``coerced`` untouched when
    ``review`` is None or the memory is missing."""
    if review is None:
        return coerced
    if review not in ("", *review_mark.REVIEW_VALUES):
        raise ValueError('review must be "history" (mark as reviewed history) or "" (clear)')
    if existing is None:
        return coerced
    base = coerced if coerced is not None else existing.metadata
    if review == "":
        cleared = review_mark.without_review(base)
        return coerced if cleared is None else cleared
    author = author_for(ctx.transport, request_grant(ctx.transport, ctx.stdio_grant))
    final = content if content is not None else existing.content
    return review_mark.with_history_mark(base, final, author)
