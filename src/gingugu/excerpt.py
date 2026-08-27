"""Reading *inside* one memory: character ranges and in-body matches.

Recall answers "which memory?". This answers "where in it?" - the question
that had no answer between a full body and a ~200-char compact summary. Our
memories run to several KB; asking whether one of them mentions a release
policy, and where, meant pulling the whole thing into context.

Deliberately dumb by design: exact character offsets and a literal substring
scan. No ranking, no stemming, no model. A caller asking "where does this say
X" wants the places it literally says X, in the order they appear, with the
offsets to read more around them - and wants the same answer every time.
"""

from __future__ import annotations

# Characters of surrounding context returned on each side of a match. About a
# line and a half either way: enough to see the sentence a hit sits in without
# turning a 20-match scan back into a full-body read.
DEFAULT_CONTEXT_CHARS = 120
MAX_CONTEXT_CHARS = 1000

# Ceiling on matches returned in one call. A scan of a large body against a
# common word can match hundreds of times; the cap bounds the payload while
# ``total_matches`` still reports the true count.
DEFAULT_MAX_MATCHES = 10
MAX_MATCHES_CAP = 100

_ELLIPSIS = "…"


def clamp_range(length: int, start: int | None, end: int | None) -> tuple[int, int]:
    """Resolve a caller's ``start``/``end`` into a valid slice of ``length``.

    Omitted bounds mean "from the beginning" and "to the end". Negative values
    are clamped to 0 rather than wrapping python-style: a memory offset is a
    position in a document, and silently reading from the far end because a
    subtraction went negative is worse than reading from the start.
    """
    lo = 0 if start is None else max(0, min(start, length))
    hi = length if end is None else max(0, min(end, length))
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def line_of(content: str, offset: int) -> int:
    """1-indexed line number containing ``offset``."""
    return content.count("\n", 0, offset) + 1


def _window(content: str, start: int, end: int, context_chars: int) -> str:
    """Readable excerpt around ``[start, end)`` with ellipses where clipped.

    Whitespace is collapsed so a match spanning a line break renders as one
    readable string. The exact offsets travel alongside in the match record,
    so precision is preserved where it matters - in the numbers, not the prose.
    """
    lo = max(0, start - context_chars)
    hi = min(len(content), end + context_chars)
    text = " ".join(content[lo:hi].split())
    if lo > 0:
        text = _ELLIPSIS + text
    if hi < len(content):
        text = text + _ELLIPSIS
    return text


def find_matches(
    content: str,
    query: str,
    *,
    case_sensitive: bool = False,
    max_matches: int = DEFAULT_MAX_MATCHES,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    start: int = 0,
    end: int | None = None,
) -> tuple[list[dict], int]:
    """Literal, non-overlapping matches of ``query`` within ``content``.

    Returns ``(matches, total)``: at most ``max_matches`` records, plus the
    true count of every match in range. Reporting the total separately is what
    keeps the cap honest - a caller that gets 10 back can tell whether that was
    all of them or the first 10 of 300, which decides whether narrowing the
    query is worth it.

    Offsets are absolute positions in the full ``content``, not relative to a
    windowed ``start``, so they can be fed straight back as a range read.
    """
    if not query:
        return [], 0
    stop = len(content) if end is None else end
    haystack = content if case_sensitive else content.lower()
    needle = query if case_sensitive else query.lower()

    matches: list[dict] = []
    total = 0
    cursor = start
    while cursor < stop:
        found = haystack.find(needle, cursor, stop)
        if found < 0:
            break
        total += 1
        if len(matches) < max_matches:
            match_end = found + len(needle)
            matches.append(
                {
                    "start": found,
                    "end": match_end,
                    "line": line_of(content, found),
                    "excerpt": _window(content, found, match_end, context_chars),
                }
            )
        # Advance past the whole match: overlapping hits of a self-overlapping
        # needle ("aa" in "aaa") would otherwise report the same text twice.
        cursor = found + len(needle)
    return matches, total
