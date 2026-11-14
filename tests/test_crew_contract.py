"""The startup contract and the crew.

A machine on the crew's brain opts in with ``MEMORY_CREW=1``: ``crew`` joins
its floor, so every session loads the commons. A crew mate (a local brain
linked by ``MEMORY_CREW_URL``) loads the commons from its ``crew`` server, and
a mate with no name yet is told to choose one before anything else. Nobody
else's contract changes.
"""

from __future__ import annotations

import pytest

from tests.test_board_watch_contract import CONTRACTS, _load


@pytest.fixture(params=sorted(CONTRACTS))
def contract(request, monkeypatch):
    for key in ("MEMORY_PERSONA", "MEMORY_CREW", "MEMORY_CREW_URL"):
        monkeypatch.delenv(key, raising=False)
    module = _load(CONTRACTS[request.param])
    return lambda: module.build_startup_contract("/home/me/gingugu")


def test_no_crew_no_change(contract, monkeypatch):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    text = contract()
    assert 'namespace="crow,beepboop,gingugu"' in text and "crew" not in text


def test_an_owner_on_the_crew_brain_loads_the_commons(contract, monkeypatch):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv("MEMORY_CREW", "1")
    assert 'namespace="crow,beepboop,crew,gingugu"' in contract()


def test_a_crew_mate_loads_the_commons_from_its_crew_server(contract, monkeypatch):
    monkeypatch.setenv("MEMORY_PERSONA", "polly")
    monkeypatch.setenv("MEMORY_CREW_URL", "https://pi.test:8765")
    text = contract()
    assert 'mcp__crew__memory_context(namespace="crew"' in text
    assert 'namespace="crow,polly,gingugu"' in text  # its own brain, unchanged
    assert "choose" not in text


def test_a_nameless_crew_mate_is_told_to_choose_a_name(contract, monkeypatch):
    monkeypatch.setenv("MEMORY_CREW_URL", "https://pi.test:8765")
    text = contract()
    assert "no name" in text and "MEMORY_PERSONA" in text
    assert text.rstrip().endswith("=== END CONTRACT ===")
