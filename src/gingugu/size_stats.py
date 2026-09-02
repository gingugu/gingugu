"""Character cost of the corpus, and of the part of it that is unconditional.

Split from ``stats.py`` for the same reason ``graph_stats.py`` was: the health
overview composes independent measurements, and each one owns its own SQL.

Counts are the metric that is easy to get, so counts are the metric that gets
used - and a store can be correct in composition while a single entry eats the
budget. Only length shows that, so length is reported alongside the counts
rather than left to whoever thinks to run the query by hand.
"""

from __future__ import annotations

import sqlite3


def compute_size(conn: sqlite3.Connection, ns_clause: str, ns_params: tuple) -> dict:
    """Total, mean, pinned and largest-pinned character counts.

    ``pinned_chars`` is the number that matters most. Pins load at every
    session start, ahead of and exempt from ranking, so they are paid for on
    every call whether or not they are relevant to it - the one part of the
    store with a guaranteed, recurring context cost.

    ``largest_pinned_chars`` is the skew check. A tier is not described by how
    many pins it holds: when one pin approaches the tier total, the tier IS
    that pin, and adding well-chosen pins to it will not fix that.

    ``LENGTH`` counts characters rather than bytes on a TEXT value, which is
    the right unit here - it tracks what a tokenizer sees far better than the
    UTF-8 byte count would.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(title) + LENGTH(content)), 0) AS total, "
        "COALESCE(SUM(CASE WHEN pinned = 1 THEN LENGTH(title) + LENGTH(content) END), 0) "
        "AS pinned_total, "
        "COALESCE(MAX(CASE WHEN pinned = 1 THEN LENGTH(title) + LENGTH(content) END), 0) "
        f"AS pinned_max, COUNT(*) AS n FROM memories{ns_clause}",
        ns_params,
    ).fetchone()
    total_chars = row["total"]
    return {
        "total_chars": total_chars,
        "mean_chars": round(total_chars / row["n"]) if row["n"] else 0,
        "pinned_chars": row["pinned_total"],
        "largest_pinned_chars": row["pinned_max"],
    }
