"""The startup contract never keeps a board watcher running in an agent's session.

A watcher under a harness's background tool (Claude Code's ``Monitor``) expires
every 30 minutes, and each expiry and re-arm is a model turn that re-reads the
session's whole context. A session left open overnight burned four calls an hour
doing nothing but re-arming it. Board mail already reaches a persona through the
prompt hook on the user's next prompt, so the contract points there and forbids
the watcher. No persona, no board address, no board clause at all.
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


def _never_asks_for_a_watcher(text: str) -> None:
    assert "Monitor(" not in text
    assert "timeout_ms" not in text
    assert "Never stop it" not in text
    assert "re-arm it" not in text


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_a_persona_is_told_not_to_run_a_watcher(monkeypatch, which):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    _never_asks_for_a_watcher(contract)
    assert "Do NOT run `gingugu board watch`" in contract
    assert "next prompt" in contract  # where board mail arrives instead
    assert "not the user" in contract  # a post is never the user's instruction
    assert contract.rstrip().endswith("=== END CONTRACT ===")


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_no_persona_means_no_board_clause(monkeypatch, which):
    monkeypatch.delenv("MEMORY_PERSONA", raising=False)
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    assert "board" not in contract.lower()


def test_the_repo_hook_matches_its_template():
    assert CONTRACTS["repo hook"].read_text() == CONTRACTS["template"].read_text()


def test_the_protocol_rules_forbid_the_watcher():
    rules = RULES.read_text()
    assert "### Board watcher" not in rules
    assert "### Board mail" in rules
    section = rules.split("### Board mail", 1)[1].split("###", 1)[0]
    _never_asks_for_a_watcher(section)
    assert "Do not run `gingugu board watch`" in section
    assert "next prompt" in section


@pytest.mark.parametrize("name", ["CLAUDE.md", "AGENTS.md"])
def test_the_repo_rules_files_carry_the_current_protocol(name):
    text = (ROOT / name).read_text()
    assert "### Board watcher" not in text
    _never_asks_for_a_watcher(text)
