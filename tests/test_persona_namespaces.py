"""MEMORY_PERSONA: a persona's own namespace loads after crow, before the repo.

crow is the layer every persona shares; a persona's self (reflections, its own
failure modes, opinions) lives in its own namespace. The hooks - involuntary
recall, tripwires - and the SessionStart contract all add it when set, and an
unset or malformed value changes nothing (hooks never crash).
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from gingugu.prompt_hook import namespaces_for

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    "template": ROOT / "src/gingugu/bootstrap/templates/session_start.py.tmpl",
    "repo hook": ROOT / ".claude/hooks/session_start.py",
}


@pytest.fixture(autouse=True)
def no_persona(monkeypatch):
    monkeypatch.delenv("MEMORY_PERSONA", raising=False)


def _load(path: Path):
    loader = SourceFileLoader(f"contract_{path.stem}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# --- namespaces_for: recall + tripwires --------------------------------------


def test_no_persona_is_unchanged():
    assert namespaces_for("/home/me/gingugu") == ["crow", "gingugu"]


def test_persona_sits_between_crow_and_the_repo(monkeypatch):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    assert namespaces_for("/home/me/gingugu") == ["crow", "beepboop", "gingugu"]


@pytest.mark.parametrize(
    "cwd, expected", [("/x/beepboop", ["crow", "beepboop"]), ("/x/crow", ["crow", "beepboop"])]
)
def test_persona_never_duplicates(monkeypatch, cwd, expected):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    assert namespaces_for(cwd) == expected


@pytest.mark.parametrize("bad", ["", "  ", "bad name!", "../etc", "a" * 65, "crow"])
def test_a_malformed_or_crow_persona_is_ignored(monkeypatch, bad):
    monkeypatch.setenv("MEMORY_PERSONA", bad)
    assert namespaces_for("/home/me/gingugu") == ["crow", "gingugu"]


# --- the SessionStart contract -----------------------------------------------


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_contract_loads_the_persona(monkeypatch, which):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    assert 'memory_context(namespace="crow,beepboop,gingugu"' in contract
    assert 'memory_stats(namespace="crow,beepboop,gingugu")' in contract


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_contract_without_a_persona_is_unchanged(which):
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    assert 'memory_context(namespace="crow,gingugu"' in contract
    assert 'memory_stats(namespace="crow,gingugu")' in contract


@pytest.mark.parametrize("which", sorted(CONTRACTS))
def test_contract_ignores_a_malformed_persona(monkeypatch, which):
    monkeypatch.setenv("MEMORY_PERSONA", 'x", namespace="all')
    contract = _load(CONTRACTS[which]).build_startup_contract("/home/me/gingugu")
    assert 'memory_context(namespace="crow,gingugu"' in contract
