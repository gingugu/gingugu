"""The demo brain's activity: recall history, a query log, claims, tripwires.

Shaped like the real thing so the Explorer's synapse and history views have
something true to show: a recall stamps every memory it returned with ONE
instant (as ``access.record`` does), most memories are touched now and then, a
share have rested for months, and ``access_count`` / ``last_accessed`` are
derived from the log rather than invented beside it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from demo_corpus import CORE, QUERIES, THINGS, VERBS

QUERY_COUNT = 450
QUERY_DAYS = 30
DORMANT_SHARE = 0.22
TOOLS = ["recall", "recall", "context", "search"]
WIRES = [r"rm -rf .*backups", r"git push .*--force", r"DROP TABLE", r"curl .*\| *sh",
         r"chmod -R 777", r"--no-verify"]  # fmt: skip


def _queries(conn: sqlite3.Connection, gen, by_ns: dict, now: datetime) -> list[tuple]:
    rows: list[tuple] = []
    names = list(by_ns)
    for i in range(QUERY_COUNT):
        ns = gen.pick(names)
        hits = gen.r.sample(by_ns[ns], k=min(len(by_ns[ns]), gen.r.randint(3, 6)))
        at = gen.when(now - timedelta(days=QUERY_DAYS), now).isoformat()
        session = f"demo-session-{i // 15}"
        topic = gen.pick(hits[0]["tags"]).replace("-", " ")
        text = gen.pick(QUERIES).format(
            verb=gen.pick(VERBS), topic=topic, thing=gen.pick(THINGS), failure="fails"
        )
        scope = [CORE, ns] if ns != CORE else [CORE]
        conn.execute(
            "INSERT INTO query_log (id, session_id, tool, query, namespaces, result_ids,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gen.uid(), session, gen.pick(TOOLS), text, json.dumps(scope),
             json.dumps([h["id"] for h in hits]), at),
        )  # fmt: skip
        rows += [(gen.uid(), h["id"], at, session) for h in hits]
    return rows


def _background(gen, memories: list[dict], now: datetime) -> list[tuple]:
    rows: list[tuple] = []
    for m in memories:
        created = datetime.fromisoformat(m["created_at"])
        hi = now
        if gen.r.random() < DORMANT_SHARE and created < now - timedelta(days=100):
            hi = now - timedelta(days=95)  # rested for months: dormant, never gone
        for _ in range(min(60, int(gen.r.paretovariate(1.4)) - 1)):
            rows.append((gen.uid(), m["id"], gen.when(created, hi).isoformat(), "demo-bg"))
    return rows


def _claims(conn: sqlite3.Connection, gen, by_ns: dict, now: datetime) -> None:
    projects = [ns for ns in by_ns if ns != CORE]
    for k in range(70):
        ns = gen.pick(projects)
        mem = gen.pick(by_ns[ns])
        resolved = gen.r.random() < 0.6
        conn.execute(
            "INSERT OR IGNORE INTO memory_claims (id, memory_id, kind, ref, state,"
            " resolved_state, resolved_at, created_at) VALUES (?, ?, 'pr', ?, ?, ?, ?, ?)",
            (gen.uid(), mem["id"], f"{ns}#{k + 1}", "resolved" if resolved else "open",
             "merged" if resolved else None, now.isoformat() if resolved else None,
             mem["created_at"]),
        )  # fmt: skip


def _tripwires(conn: sqlite3.Connection, gen, by_ns: dict) -> None:
    rules = [m for m in by_ns[CORE] if m["type"] == "preference"][:15]
    for m in rules:
        conn.execute(
            "INSERT INTO tripwires (id, memory_id, tool_pattern, input_pattern, created_at)"
            " VALUES (?, ?, 'Bash', ?, ?)",
            (gen.uid(), m["id"], gen.pick(WIRES), m["created_at"]),
        )


def add_activity(conn: sqlite3.Connection, gen, memories, by_ns, now: datetime) -> None:
    rows = _queries(conn, gen, by_ns, now) + _background(gen, memories, now)
    conn.executemany(
        "INSERT INTO access_log (id, memory_id, accessed_at, context) VALUES (?, ?, ?, ?)", rows
    )
    conn.execute(
        "UPDATE memories SET"
        " access_count = (SELECT COUNT(*) FROM access_log a WHERE a.memory_id = memories.id),"
        " last_accessed = COALESCE((SELECT MAX(accessed_at) FROM access_log a"
        " WHERE a.memory_id = memories.id), created_at)"
    )
    _claims(conn, gen, by_ns, now)
    _tripwires(conn, gen, by_ns)
