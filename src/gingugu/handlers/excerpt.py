"""Read inside a single memory: ``memory_excerpt``.

The retrieval surface answers "which memory?"; this answers "where in it?".
Kept in its own module rather than bolted onto ``search.py`` because it is a
different question with a different shape - it takes one memory id, never
ranks, and never returns more than one memory.
"""

from __future__ import annotations

import logging

from ..excerpt import (
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_MAX_MATCHES,
    MAX_CONTEXT_CHARS,
    MAX_MATCHES_CAP,
    clamp_range,
    find_matches,
    line_of,
)
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_excerpt(
        memory_id: str,
        query: str | None = None,
        start: int | None = None,
        end: int | None = None,
        max_matches: int = DEFAULT_MAX_MATCHES,
        context_chars: int = DEFAULT_CONTEXT_CHARS,
        case_sensitive: bool = False,
    ) -> dict:
        """Search or slice WITHIN one memory's body, without loading the whole thing.

        Recall and search answer "which memory?"; this answers "where in it?".
        Between a full body and a ~200-char compact summary there was nothing:
        asking whether a long memory mentions a particular decision, and where,
        meant pulling every byte of it into context. Use this instead once you
        know which memory you want.

        Two modes, composable:
        - **Find**: pass ``query`` for a literal, case-insensitive substring
          scan. Each match returns its ``start``/``end`` character offsets, its
          1-indexed ``line``, and an ``excerpt`` with ``context_chars`` of
          surrounding text on each side. ``total_matches`` is the true count
          even when ``max_matches`` caps what comes back, so you can tell "that
          was all of them" from "that was the first 10 of 300".
        - **Slice**: pass ``start`` and/or ``end`` character offsets to read an
          exact range. Omitted bounds mean start-of-body and end-of-body. Feed
          back the offsets from a find to read the full passage around a hit.

        Passing both searches only inside the range, with offsets still
        reported absolute against the full body.

        The scan is literal and deterministic: no ranking, no stemming, no
        model. Asking twice gives the same answer in the same order, and
        matches come back in the order they appear in the text, never by
        relevance. ``length`` (total characters) and ``lines`` come back on
        every call, so a first call with no query is a cheap way to size a
        memory before deciding how to read it.

        Reading a memory this way credits it as a real access, the same as
        naming it in ``memory_search(ids=...)``."""
        try:
            if max_matches < 1 or max_matches > MAX_MATCHES_CAP:
                return _err(f"max_matches must be between 1 and {MAX_MATCHES_CAP}")
            if context_chars < 0 or context_chars > MAX_CONTEXT_CHARS:
                return _err(f"context_chars must be between 0 and {MAX_CONTEXT_CHARS}")

            mem = ctx.store.get(memory_id, record_access=False)
            if mem is None:
                return _err(f"memory {memory_id!r} not found")

            content = mem.content
            payload: dict = {
                "ok": True,
                "memory_id": mem.id,
                "title": mem.title,
                "length": len(content),
                "lines": line_of(content, len(content)),
            }

            has_range = start is not None or end is not None
            lo, hi = clamp_range(len(content), start, end)
            if has_range:
                payload["start"] = lo
                payload["end"] = hi
                # The slice itself is only returned when no query narrows it.
                # Otherwise the caller asked for matches, and shipping the whole
                # range alongside them defeats the point of not loading the body.
                if query is None:
                    payload["text"] = content[lo:hi]

            if query is not None:
                if not query.strip():
                    return _err("query must not be empty; omit it to slice by offset instead")
                matches, total = find_matches(
                    content,
                    query,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                    context_chars=context_chars,
                    start=lo,
                    end=hi,
                )
                payload["query"] = query
                payload["matches"] = matches
                payload["total_matches"] = total
                payload["truncated"] = total > len(matches)
            elif not has_range:
                # Neither mode requested: the size fields above are the answer,
                # and they are what you need to choose a range. Say so rather
                # than silently returning a bodyless result that reads as a bug.
                payload["hint"] = (
                    "no query or range given - returned size only; "
                    "pass query= to find text or start=/end= to slice"
                )

            # An explicit read of a memory the caller named by id. No spreading
            # activation: like search, this traverses no relations.
            ctx.store.record_accesses([mem.id])
            return payload
        except Exception as exc:
            logger.exception("memory_excerpt failed")
            return _err(f"memory_excerpt failed: {exc}")
