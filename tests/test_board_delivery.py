"""The board's delivery receipt: does the recipient's grant see the namespace?

A post stored where its recipient cannot read is never delivered, and the
poster used to get a bare ``ok``. A persona is not a token, so the receipt has
three answers: visible, not visible, or unverified when no client of that
persona is known to the brain.
"""

from __future__ import annotations

import httpx2

from gingugu import board_delivery
from gingugu.derived_tokens import DerivedTokens
from gingugu.grants import Grant
from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import make_brain, serving
from tests.test_board_twins_http import _call


def _g(name: str, spec: dict[str, str]) -> Grant:
    return Grant(f"derived:{name}", spec, derived=True)


# --- receipt ------------------------------------------------------------------


def test_broadcast_has_no_receipt():
    assert board_delivery.receipt("*", "gingugu", [_g("tyrone", {"tyrone": "write"})]) is None


def test_no_known_client_is_unverified():
    out = board_delivery.receipt("tyrone", "gingugu", [])
    assert out["recipient"] == "tyrone"
    assert out["visible"] is None
    assert "unverified" in out["note"]


def test_a_client_that_reads_the_namespace_is_visible():
    grants = [_g("tyrone", {"tyrone": "write"}), _g("tyrone", {"gingugu": "read"})]
    out = board_delivery.receipt("tyrone", "gingugu", grants)
    assert out["visible"] is True


def test_wildcard_read_is_visible():
    out = board_delivery.receipt("tyrone", "gingugu", [_g("tyrone", {"*": "read"})])
    assert out["visible"] is True


def test_every_client_blind_to_the_namespace_is_not_visible():
    grants = [_g("tyrone", {"tyrone": "write"}), _g("tyrone", {"crow": "read"})]
    out = board_delivery.receipt("tyrone", "gingugu", grants)
    assert out["visible"] is False
    assert "gingugu" in out["note"]
    assert "tyrone" in out["note"]


# --- whose grants count ---------------------------------------------------------


def test_recipient_grants_reads_live_derived_tokens_by_persona(tmp_path):
    derived = DerivedTokens()
    derived.mint(_g("tyrone", {"tyrone": "write"}), 60)
    derived.mint(_g("beepboop", {"*": "write"}), 60)
    found = board_delivery.recipient_grants("tyrone", derived, None)
    assert [g.namespaces for g in found] == [{"tyrone": "write"}]


def test_expired_derived_tokens_do_not_count():
    now = [0.0]
    derived = DerivedTokens(clock=lambda: now[0])
    derived.mint(_g("tyrone", {"gingugu": "read"}), 10)
    now[0] = 11.0
    assert board_delivery.recipient_grants("tyrone", derived, None) == []


def test_a_scoped_token_named_for_the_persona_counts(tmp_path):
    tokens = TokenStore(tmp_path / "serve_tokens.json")
    tokens.add("tyrone", {"tyrone": "write"})
    tokens.add_owner("macbookpro")  # a machine, not a persona
    found = board_delivery.recipient_grants("tyrone", None, tokens)
    assert [dict(g.namespaces) for g in found] == [{"tyrone": "write"}]
    assert board_delivery.recipient_grants("macbookpro", None, tokens) == []


# --- end to end over HTTP -------------------------------------------------------


async def _derive(served, owner: str, name: str, grant: str | None = None) -> str:
    body = {"name": name, **({"grant": grant} if grant else {})}
    async with httpx2.AsyncClient(timeout=10) as http:
        r = await http.post(
            f"{served.url}/token/derive",
            json=body,
            headers={"Authorization": f"Bearer {owner}"},
        )
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def test_post_reports_whether_the_recipient_can_see_it(tmp_path, monkeypatch):
    brain = make_brain(tmp_path, monkeypatch, credentials=False)
    async with serving(brain.app()) as served:
        me = await _derive(served, brain.machine, "beepboop")
        await _derive(served, brain.machine, "tyrone", "tyrone=write")

        hidden = await _call(
            served, me, "board", action="post", body="review?", to="tyrone", namespace="gingugu"
        )
        assert hidden["ok"], hidden
        assert hidden["delivery"]["visible"] is False
        assert hidden["post"]["id"]  # still stored: grants can change

        seen = await _call(
            served, me, "board", action="post", body="review?", to="tyrone", namespace="tyrone"
        )
        assert seen["delivery"]["visible"] is True

        reply = await _call(
            served, me, "board", action="reply", post_id=seen["post"]["id"], body="more"
        )
        assert reply["delivery"]["visible"] is True

        nobody = await _call(served, me, "board", action="post", body="hi", to="ghost")
        assert nobody["delivery"]["visible"] is None

        everyone = await _call(served, me, "board", action="post", body="all hands")
        assert "delivery" not in everyone
