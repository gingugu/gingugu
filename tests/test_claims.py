"""Tests for state-claim extraction, persistence, and contradiction detection.

Every case here is drawn from a real memory in a 764-memory corpus. The
extractor was measured before it was written, and each false positive it used
to produce has a test pinning it shut.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gingugu import claim_sync as cs
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


# --- extraction: a citation is not an assertion -----------------------------


def test_ref_inside_a_wikilink_asserts_nothing() -> None:
    """``[[PR #32 open: ...]]`` names another memory; it does not claim."""
    assert (
        cm.extract_claims(
            "",
            "Detail lives in [[PR #32 open: migration 006 repairs the backfill]].",
            namespace_default="gingugu",
        )
        == []
    )


def test_a_memory_titled_resolved_does_not_inherit_an_open_claim_from_a_link() -> None:
    """The worst real case, from the live corpus.

    A memory titled "RESOLVED: internal gateway crashloop" was asserting
    ``#155 open`` purely because it linked to a memory whose title said so.
    That is a wrong claim in a namespace whose default repo was correct, so
    namespace containment never covered it.
    """
    claims = cm.extract_claims(
        "RESOLVED: internal gateway crashloop was the burstable RDS",
        "The guardrail PR #155 can now be re-pointed to k8s/internal and tested here. "
        "See [[DESI-52 guardrails: PR #155 OPEN, merge HELD until DESI-58 tests it]].",
        namespace_default="devex-ai-gateway",
    )
    assert claims == []


def test_a_real_claim_survives_alongside_a_wikilink() -> None:
    """Blanking links must not cost the memory's own assertion."""
    (claim,) = cm.extract_claims(
        "",
        "PR #20 is still open. Background in [[PR #12 merged: context efficiency]].",
        namespace_default="gingugu",
    )
    assert (claim.ref, claim.state) == ("gingugu#20", cm.STATE_OPEN)


def test_blanking_a_wikilink_does_not_shift_a_later_refs_state_window() -> None:
    """Length preservation is load-bearing, not tidiness.

    The state window and the line-start quote parity both index into the same
    string. Collapsing a link instead of blanking it would drag later refs into
    a different window and silently re-scope their claims.
    """
    link = "[[" + "x" * 200 + "]]"
    (claim,) = cm.extract_claims(
        "", f"{link} PR #20 was merged last week", namespace_default="gingugu"
    )
    assert (claim.ref, claim.state) == ("gingugu#20", cm.STATE_RESOLVED)


def test_a_multiline_wikilink_is_blanked_without_losing_line_boundaries() -> None:
    """Newlines survive blanking so quote parity keeps its line anchor."""
    claims = cm.extract_claims(
        "",
        'He said "quoted" here.\n[[PR #99 open:\na title that wrapped]]\nPR #20 open',
        namespace_default="gingugu",
    )
    assert [(c.ref, c.state) for c in claims] == [("gingugu#20", cm.STATE_OPEN)]


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


# --- what a bare ref means in a namespace -----------------------------------


def test_unset_default_repo_falls_back_to_the_namespace_name(conn) -> None:
    """The one-namespace-per-repo convention, and the right default: measured
    over 764 memories it is the difference between 145 claims and 26."""
    assert cs.namespace_default_repo(conn, "ns1") == "gingugu"


def test_empty_default_repo_declares_the_namespace_is_not_a_repo(conn) -> None:
    """``crow`` is an identity namespace. There is no repo called crow, so a
    bare "PR #32" there must be dropped, not keyed to ``crow#32``."""
    conn.execute("UPDATE namespaces SET default_repo = '' WHERE id = 'ns1'")
    assert cs.namespace_default_repo(conn, "ns1") is None


def test_explicit_default_repo_wins_over_the_namespace_name(conn) -> None:
    """Lets a namespace named differently from its repo slug key bare refs."""
    conn.execute("UPDATE namespaces SET default_repo = 'litellm' WHERE id = 'ns1'")
    assert cs.namespace_default_repo(conn, "ns1") == "litellm"


def test_a_missing_namespace_drops_bare_refs(conn) -> None:
    assert cs.namespace_default_repo(conn, "nope") is None


def test_declaring_a_namespace_non_repo_clears_its_existing_claims() -> None:
    """A declaration that changes nothing already stored is not a feature.

    Claims are stored rows and ``default_repo`` is only read at extraction
    time, so without a re-derive on update the flag is inert — and there is no
    other supported way to apply it, since storage.update only re-syncs when
    the prose actually changed. Shipped inert in v0.11.0.
    """
    from gingugu.namespaces import NamespaceManager

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    manager = NamespaceManager(c, None)
    ns = manager.get_or_create("bspeagle")
    _mem(c, "m1", ns.id, "Reflection", "PR #166 is still open")
    c.commit()
    from gingugu import claim_rederive

    claim_rederive.rederive_claims(c)
    c.commit()
    assert [r[0] for r in c.execute("SELECT ref FROM memory_claims")] == ["bspeagle#166"]

    manager.update("bspeagle", default_repo="")

    assert c.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0


def test_setting_an_explicit_default_repo_rekeys_existing_claims() -> None:
    from gingugu.namespaces import NamespaceManager

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    manager = NamespaceManager(c, None)
    ns = manager.get_or_create("devex")
    _mem(c, "m1", ns.id, "Notes", "PR #166 is still open")
    c.commit()
    from gingugu import claim_rederive

    claim_rederive.rederive_claims(c)
    c.commit()

    manager.update("devex", default_repo="devex-ai-gateway")

    assert [r[0] for r in c.execute("SELECT ref FROM memory_claims")] == ["devex-ai-gateway#166"]


def test_a_namespace_update_that_leaves_default_repo_alone_does_not_touch_claims() -> None:
    """Resolution state must survive a description edit."""
    from gingugu.namespaces import NamespaceManager

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    manager = NamespaceManager(c, None)
    ns = manager.get_or_create("gingugu")
    _mem(c, "m1", ns.id, "Notes", "PR #20 is still open")
    c.commit()
    from gingugu import claim_rederive

    claim_rederive.rederive_claims(c)
    c.execute("UPDATE memory_claims SET resolved_at = '2026-02-01'")
    c.commit()

    manager.update("gingugu", description="unrelated edit")

    rows = c.execute("SELECT ref, resolved_at FROM memory_claims").fetchall()
    assert [tuple(r) for r in rows] == [("gingugu#20", "2026-02-01")]


def test_sync_claims_replaces_previous_rows(conn: sqlite3.Connection) -> None:
    _mem(conn, "m1", "ns1", "t", "PR #10 open")
    now = utcnow_iso()
    assert (
        cs.sync_claims(
            conn, "m1", cm.extract_claims("t", "PR #10 open", namespace_default="gingugu"), now=now
        )
        == 1
    )
    # the text changed: the old claim must not linger
    assert (
        cs.sync_claims(
            conn, "m1", cm.extract_claims("t", "PR #99 open", namespace_default="gingugu"), now=now
        )
        == 1
    )
    (row,) = conn.execute("SELECT ref FROM memory_claims WHERE memory_id='m1'").fetchall()
    assert row["ref"] == "gingugu#99"


def test_find_contradicted_pairs_open_with_resolved(conn: sqlite3.Connection) -> None:
    _mem(conn, "old", "ns1", "PR #10 open: serve transport", "PR #10, open, NOT merged yet")
    now = utcnow_iso()
    cs.sync_claims(
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
    hits = cs.find_contradicted(conn, namespace_id="ns1", claims=incoming)
    assert [h["id"] for h in hits] == ["old"]
    assert hits[0]["ref"] == "gingugu#10"


def test_contradiction_does_not_cross_namespaces(conn: sqlite3.Connection) -> None:
    """A bare-ref mis-key in one namespace must not reach into another."""
    _mem(conn, "old", "ns2", "PR #10 open", "PR #10, open")
    cs.sync_claims(
        conn,
        "old",
        cm.extract_claims("PR #10 open", "PR #10, open", namespace_default="other"),
        now=utcnow_iso(),
    )
    incoming = cm.extract_claims("", "PR #10 merged", namespace_default="gingugu")
    assert cs.find_contradicted(conn, namespace_id="ns1", claims=incoming) == []


def test_an_open_claim_alone_contradicts_nothing(conn: sqlite3.Connection) -> None:
    _mem(conn, "old", "ns1", "PR #10 open", "PR #10, open")
    cs.sync_claims(
        conn,
        "old",
        cm.extract_claims("PR #10 open", "PR #10, open", namespace_default="gingugu"),
        now=utcnow_iso(),
    )
    incoming = cm.extract_claims("", "PR #10 still open", namespace_default="gingugu")
    assert cs.find_contradicted(conn, namespace_id="ns1", claims=incoming) == []


def test_mark_resolved_never_touches_the_prose(conn: sqlite3.Connection) -> None:
    """The whole reason this table exists: the memory said "open" and that was
    true when written. Resolution is recorded beside it, not by rewriting it."""
    prose = "PR #10, open, NOT merged yet"
    _mem(conn, "old", "ns1", "t", prose)
    cs.sync_claims(
        conn, "old", cm.extract_claims("t", prose, namespace_default="gingugu"), now=utcnow_iso()
    )
    _mem(conn, "new", "ns1", "t2", "PR #10 merged")

    assert cs.mark_resolved(
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
    assert cs.find_contradicted(conn, namespace_id="ns1", claims=incoming) == []


def test_claims_are_deleted_with_their_memory(conn: sqlite3.Connection) -> None:
    _mem(conn, "m1", "ns1", "t", "PR #10 open")
    cs.sync_claims(
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


# --- regressions found by re-measuring against the real corpus --------------


def test_ref_inside_a_list_of_quoted_strings_is_quoted() -> None:
    """The quote scanner used to align on the wrong parity.

    Sliding a window over a comma-separated list of quoted items starts on a
    CLOSING quote, so a span matcher pairs up the `", "` separators BETWEEN
    items instead of the items themselves — and every ref read as unquoted.
    Real case: a bug report citing several stale claims side by side.
    """
    content = (
        'the stale ones were "PR #13 open: staleness review hints", '
        '"Reconciled docs/roadmap.md ... NOT yet merged/pushed", '
        '"awesome-mcp-servers PR #8072 open", "BLOCKED: waiting on Joseph".'
    )
    assert cm.extract_claims("", content, namespace_default="gingugu") == []


def test_superseded_pending_a_decision_is_still_open() -> None:
    """Real corpus case: "MR !4 appears redundant/superseded; needs a decision
    (close, or rebase)" describes the situation, not a closed MR.

    The requirement is only that it must not read as RESOLVED, since that
    would wrongly reconcile a still-open MR. Asserting nothing at all is the
    safer outcome and is what happens here - no state word survives.
    """
    claims = cm.extract_claims(
        "",
        "MR !4 now appears redundant/superseded; needs a decision (close, or rebase)",
        namespace_default="keycloakify",
    )
    assert all(c.state != cm.STATE_RESOLVED for c in claims)


def test_not_merged_yet_is_open_not_resolved() -> None:
    """Inverting a claim is the worst available failure: "NOT merged yet"
    contains the word `merged`, and resolved is tested before open."""
    for phrasing in (
        "PR #10, open, NOT merged yet",
        "PR #10 was never merged",
        "PR #10 is not yet merged",
    ):
        (claim,) = cm.extract_claims("", phrasing, namespace_default="gingugu")
        assert claim.state == cm.STATE_OPEN, phrasing


# --- the write-time hook on the tool surface --------------------------------


@pytest.mark.asyncio
async def test_store_surfaces_contradicted_memories(server) -> None:
    """The payoff: recording a resolution makes stale claims knowable AT WRITE
    TIME, when the caller is already thinking about that exact PR."""
    stale = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "SHIPPED (PR #10, open): serve transport",
                "content": "Built on branch feature/serve-transport. PR #10, open, NOT merged yet.",
                "type": "decision",
            },
        )
    )
    assert "contradicted_memories" not in stale  # nothing to contradict yet

    resolving = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "v0.4.0 released",
                "content": "PR #10 merged to main, branch deleted.",
                "type": "workflow",
            },
        )
    )
    hits = resolving["contradicted_memories"]
    assert [h["id"] for h in hits] == [stale["memory"]["id"]]
    assert hits[0]["ref"] == "gingugu#10"
    assert (hits[0]["asserts"], hits[0]["now"]) == ("open", "resolved")


@pytest.mark.asyncio
async def test_update_to_a_resolution_surfaces_contradictions(server) -> None:
    stale = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "PR #20 open",
                "content": "PR #20 is open, awaiting review",
                "type": "workflow",
            },
        )
    )
    other = _payload(
        await server.call_tool(
            "memory_store",
            {"title": "notes", "content": "no refs here", "type": "context"},
        )
    )
    updated = _payload(
        await server.call_tool(
            "memory_update",
            {"memory_id": other["memory"]["id"], "content": "PR #20 merged to main"},
        )
    )
    assert [h["id"] for h in updated["contradicted_memories"]] == [stale["memory"]["id"]]


@pytest.mark.asyncio
async def test_no_contradiction_key_when_nothing_is_stale(server) -> None:
    """The key is omitted rather than empty: an always-present empty list is
    noise in every response, and this one has to stay cheap to ignore."""
    plain = _payload(
        await server.call_tool(
            "memory_store",
            {"title": "t", "content": "PR #99 merged to main", "type": "workflow"},
        )
    )
    assert "contradicted_memories" not in plain


# --- the reconciliation loop, via existing tools only -----------------------


@pytest.mark.asyncio
async def test_stats_reports_the_contradiction_backlog(server) -> None:
    await server.call_tool(
        "memory_store",
        {"title": "PR #10 open", "content": "PR #10, open, NOT merged yet", "type": "decision"},
    )
    stats = _payload(await server.call_tool("memory_stats", {}))
    assert stats["stats"]["claims"]["contradicted"] == 0  # nothing disagrees yet

    await server.call_tool(
        "memory_store", {"title": "released", "content": "PR #10 merged", "type": "workflow"}
    )
    claims = _payload(await server.call_tool("memory_stats", {}))["stats"]["claims"]
    assert claims["contradicted"] == 1
    assert claims["sample"][0]["ref"] == "gingugu#10"
    assert claims["open"] >= 1 and claims["resolved"] >= 1


@pytest.mark.asyncio
async def test_reconcile_without_editing_prose(server) -> None:
    """The whole point. A dated record that said "PR #10 open" was CORRECT on
    the day it was written. Rewriting it to stay current destroys the record,
    so resolution is recorded beside it and the body stays byte-identical."""
    prose = "Session Jun 15: PR #10, open, NOT merged yet. Waiting on Joe."
    stale = _payload(
        await server.call_tool(
            "memory_store", {"title": "session log", "content": prose, "type": "workflow"}
        )
    )
    mid = stale["memory"]["id"]
    await server.call_tool(
        "memory_store", {"title": "released", "content": "PR #10 merged", "type": "workflow"}
    )
    assert (
        _payload(await server.call_tool("memory_stats", {}))["stats"]["claims"]["contradicted"] == 1
    )

    resolved = _payload(
        await server.call_tool("memory_update", {"memory_id": mid, "resolve_claims": "gingugu#10"})
    )
    assert resolved["resolved_claims"] == ["gingugu#10"]
    assert resolved["memory"]["content"] == prose  # byte-identical

    after = _payload(await server.call_tool("memory_stats", {}))["stats"]["claims"]
    assert after["contradicted"] == 0


@pytest.mark.asyncio
async def test_resolve_claims_all(server) -> None:
    stale = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "resume",
                "content": "PR #10 open. PR #20 awaiting review. PR #30 not yet merged.",
                "type": "workflow",
            },
        )
    )
    out = _payload(
        await server.call_tool(
            "memory_update", {"memory_id": stale["memory"]["id"], "resolve_claims": "all"}
        )
    )
    assert sorted(out["resolved_claims"]) == ["gingugu#10", "gingugu#20", "gingugu#30"]


@pytest.mark.asyncio
async def test_resolving_an_unknown_ref_reports_nothing_changed(server) -> None:
    stored = _payload(
        await server.call_tool(
            "memory_store", {"title": "t", "content": "PR #10 open", "type": "workflow"}
        )
    )
    out = _payload(
        await server.call_tool(
            "memory_update", {"memory_id": stored["memory"]["id"], "resolve_claims": "gingugu#999"}
        )
    )
    assert out["resolved_claims"] == []
