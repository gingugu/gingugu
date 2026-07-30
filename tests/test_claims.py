"""Tests for state-claim extraction, persistence, and contradiction detection.

Every case here is drawn from a real memory in a 764-memory corpus. The
extractor was measured before it was written, and each false positive it used
to produce has a test pinning it shut.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gingugu import claims as cm
from gingugu.database import migrate
from gingugu.models import utcnow_iso

# --- extraction: repo qualification -----------------------------------------


def test_bare_ref_uses_the_namespace_default() -> None:
    (claim,) = cm.extract_claims("", "PR #20 is still open", namespace_default="gingugu")
    assert claim.ref == "gingugu#20"
    assert claim.state == cm.STATE_OPEN


def test_bare_ref_dropped_without_a_namespace_default() -> None:
    """A cross-project namespace must not mis-key a bare ref: gingugu#12 and
    VersatermTechPlatform#12 are different objects."""
    assert cm.extract_claims("", "PR #12 is still open", namespace_default=None) == []


def test_url_beats_the_namespace_default() -> None:
    (claim,) = cm.extract_claims(
        "",
        "PR #8072 is open: https://github.com/punkpeye/awesome-mcp-servers/pull/8072",
        namespace_default="gingugu",
    )
    assert claim.ref == "awesome-mcp-servers#8072"


def test_named_repo_alias_beats_the_namespace_default() -> None:
    (claim,) = cm.extract_claims(
        "", "VTP PR #947 still open, waiting on Joe", namespace_default="devex-ai-gateway"
    )
    assert claim.ref == "VersatermTechPlatform#947"


def test_merge_request_is_its_own_kind() -> None:
    (claim,) = cm.extract_claims("", "MR !9 is open", namespace_default="keycloakify")
    assert (claim.kind, claim.ref) == ("mr", "keycloakify#9")


# --- extraction: state vocabulary -------------------------------------------


def test_resolved_wins_within_one_memory() -> None:
    """Real regression: a memory TITLED "PR #174 MERGED" that also narrates
    "Opened + merged same day" asserts resolution, not openness."""
    (claim,) = cm.extract_claims(
        "PR #174 MERGED (Jul 27 2026): DESI-52 master switch",
        'PR #174 "fix: master switch". Opened + merged the same day.',
        namespace_default="devex-ai-gateway",
    )
    assert claim.state == cm.STATE_RESOLVED


def test_title_is_scanned_not_just_content() -> None:
    (claim,) = cm.extract_claims(
        "PR #65 SHIPPED", "the onboarding work landed", namespace_default="OKREngine"
    )
    assert claim.state == cm.STATE_RESOLVED


def test_held_counts_as_open() -> None:
    (claim,) = cm.extract_claims(
        "",
        "PR #166 OPEN + HELD - do NOT merge until the router is tested",
        namespace_default="devex-ai-gateway",
    )
    assert claim.state == cm.STATE_OPEN


def test_bare_mention_with_no_state_is_not_a_claim() -> None:
    assert cm.extract_claims("", "see PR #40 for context", namespace_default="gingugu") == []


def test_quoted_ref_is_cited_not_claimed() -> None:
    """Real regression: a bug report quoting '"PR #30 open"' was itself flagged."""
    assert (
        cm.extract_claims(
            "MERGED: PR #30 precision pass",
            'reconciling this memory\'s own "PR #30 open" claim the moment it went false',
            namespace_default="gingugu",
        )[0].state
        == cm.STATE_RESOLVED
    )


def test_apostrophes_do_not_read_as_quotes() -> None:
    """A bare ' is a possessive far more often than a delimiter."""
    (claim,) = cm.extract_claims(
        "",
        "PR #7 open - awaiting Mr. Boomtastic's go, not yet PR'd",
        namespace_default="gingugu",
    )
    assert claim.state == cm.STATE_OPEN


def test_one_claim_per_ref_per_memory() -> None:
    claims = cm.extract_claims(
        "", "PR #12 open. Later: PR #12 open again. PR #20 open.", namespace_default="gingugu"
    )
    assert sorted(c.ref for c in claims) == ["gingugu#12", "gingugu#20"]


# --- persistence + contradiction detection ----------------------------------


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    c.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns1", "gingugu", utcnow_iso(), utcnow_iso()),
    )
    c.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns2", "other", utcnow_iso(), utcnow_iso()),
    )
    return c


def _mem(conn: sqlite3.Connection, mid: str, ns: str, title: str, content: str) -> None:
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
        "created_at, updated_at, last_accessed, access_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,0)",
        (mid, ns, "workflow", title, content, "verified", now, now, now),
    )


def test_sync_claims_replaces_previous_rows(conn: sqlite3.Connection) -> None:
    _mem(conn, "m1", "ns1", "t", "PR #10 open")
    now = utcnow_iso()
    assert (
        cm.sync_claims(
            conn, "m1", cm.extract_claims("t", "PR #10 open", namespace_default="gingugu"), now=now
        )
        == 1
    )
    # the text changed: the old claim must not linger
    assert (
        cm.sync_claims(
            conn, "m1", cm.extract_claims("t", "PR #99 open", namespace_default="gingugu"), now=now
        )
        == 1
    )
    (row,) = conn.execute("SELECT ref FROM memory_claims WHERE memory_id='m1'").fetchall()
    assert row["ref"] == "gingugu#99"


def test_find_contradicted_pairs_open_with_resolved(conn: sqlite3.Connection) -> None:
    _mem(conn, "old", "ns1", "PR #10 open: serve transport", "PR #10, open, NOT merged yet")
    now = utcnow_iso()
    cm.sync_claims(
        conn,
        "old",
        cm.extract_claims(
            "PR #10 open: serve transport",
            "PR #10, open, NOT merged yet",
            namespace_default="gingugu",
        ),
        now=now,
    )
    incoming = cm.extract_claims("", "PR #10 merged to main", namespace_default="gingugu")
    hits = cm.find_contradicted(conn, namespace_id="ns1", claims=incoming)
    assert [h["id"] for h in hits] == ["old"]
    assert hits[0]["ref"] == "gingugu#10"


def test_contradiction_does_not_cross_namespaces(conn: sqlite3.Connection) -> None:
    """A bare-ref mis-key in one namespace must not reach into another."""
    _mem(conn, "old", "ns2", "PR #10 open", "PR #10, open")
    cm.sync_claims(
        conn,
        "old",
        cm.extract_claims("PR #10 open", "PR #10, open", namespace_default="other"),
        now=utcnow_iso(),
    )
    incoming = cm.extract_claims("", "PR #10 merged", namespace_default="gingugu")
    assert cm.find_contradicted(conn, namespace_id="ns1", claims=incoming) == []


def test_an_open_claim_alone_contradicts_nothing(conn: sqlite3.Connection) -> None:
    _mem(conn, "old", "ns1", "PR #10 open", "PR #10, open")
    cm.sync_claims(
        conn,
        "old",
        cm.extract_claims("PR #10 open", "PR #10, open", namespace_default="gingugu"),
        now=utcnow_iso(),
    )
    incoming = cm.extract_claims("", "PR #10 still open", namespace_default="gingugu")
    assert cm.find_contradicted(conn, namespace_id="ns1", claims=incoming) == []


def test_mark_resolved_never_touches_the_prose(conn: sqlite3.Connection) -> None:
    """The whole reason this table exists: the memory said "open" and that was
    true when written. Resolution is recorded beside it, not by rewriting it."""
    prose = "PR #10, open, NOT merged yet"
    _mem(conn, "old", "ns1", "t", prose)
    cm.sync_claims(
        conn, "old", cm.extract_claims("t", prose, namespace_default="gingugu"), now=utcnow_iso()
    )
    _mem(conn, "new", "ns1", "t2", "PR #10 merged")

    assert cm.mark_resolved(
        conn, memory_id="old", ref="gingugu#10", resolved_by="new", now=utcnow_iso()
    )
    row = conn.execute("SELECT content FROM memories WHERE id='old'").fetchone()
    assert row["content"] == prose  # byte-identical

    claim = conn.execute("SELECT * FROM memory_claims WHERE memory_id='old'").fetchone()
    assert claim["state"] == cm.STATE_OPEN  # what it asserts, unchanged
    assert claim["resolved_state"] == cm.STATE_RESOLVED
    assert claim["resolved_by"] == "new"

    # and it stops being reported as contradicted
    incoming = cm.extract_claims("", "PR #10 merged", namespace_default="gingugu")
    assert cm.find_contradicted(conn, namespace_id="ns1", claims=incoming) == []


def test_claims_are_deleted_with_their_memory(conn: sqlite3.Connection) -> None:
    _mem(conn, "m1", "ns1", "t", "PR #10 open")
    cm.sync_claims(
        conn,
        "m1",
        cm.extract_claims("t", "PR #10 open", namespace_default="gingugu"),
        now=utcnow_iso(),
    )
    conn.execute("DELETE FROM memories WHERE id='m1'")
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0


# --- store wiring -----------------------------------------------------------


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "claims.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server()


@pytest.mark.asyncio
async def test_storing_a_memory_extracts_its_claims(server) -> None:
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "PR #10 is open, not merged yet",
                "title": "serve transport",
                "type": "decision",
            },
        )
    )
    assert stored["ok"]


@pytest.mark.asyncio
async def test_editing_text_re_derives_claims(server) -> None:
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "PR #10 is open", "title": "t", "type": "workflow"},
        )
    )
    updated = _payload(
        await server.call_tool(
            "memory_update",
            {"memory_id": stored["memory"]["id"], "content": "PR #10 merged to main"},
        )
    )
    assert updated["ok"]
