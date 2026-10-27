"""The startup contract keeps a persona's board watcher running all session.

Other agents reach a persona through the board, and a watcher is the only thing
that delivers a post while the agent is mid-task: the prompt hook only sees mail
when the user next types. A watcher that expires, or is stopped, goes quiet with
no sign. So the contract tells a persona to start one, never stop it, and re-arm
it the moment it expires. No persona, no board address, no watcher.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    "template": ROOT / "src/gingugu/bootstrap/templates/session_start.py.tmpl",
    "repo hook": ROOT / ".claude/hooks/session_start.py",
}
RULES = ROOT / "src/gingugu/bootstrap/templates/rules_protocol.md.tmpl"


def _load(path: Path):
    loader = importlib.machinery.SourceFileLoader(f"contract_{path.stem}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_a_persona_is_told_to_keep_its_watcher_running(monkeypatch, which):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    assert "gingugu board watch" in contract
    assert "Monitor" in contract
    assert "Never stop it" in contract
    assert "re-arm" in contract
    assert "not the user" in contract  # a post is never the user's instruction
    assert contract.rstrip().endswith("=== END CONTRACT ===")


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_no_persona_means_no_watcher(monkeypatch, which):
    monkeypatch.delenv("MEMORY_PERSONA", raising=False)
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    assert "board watch" not in contract


def test_the_repo_hook_matches_its_template():
    assert CONTRACTS["repo hook"].read_text() == CONTRACTS["template"].read_text()


def test_the_protocol_rules_carry_the_watcher_rule():
    rules = RULES.read_text()
    assert "### Board watcher" in rules
    section = rules.split("### Board watcher", 1)[1].split("###", 1)[0]
    assert "gingugu board watch" in section
    assert "Never stop it" in section and "re-arm" in section
