"""A folder name the brain would refuse never costs the whole remote hook.

The brain's hook routes accept only namespace names of letters, digits, '.',
'_' and '-' and answer 400 to anything else. The repo namespace is the cwd
basename, unvalidated, so a folder like "My Repo" used to turn every recall
and tripwire request into a 400 - a silent hook for crow and the persona too.
Remote mode now leaves such a name out of what it sends; local mode, where
any namespace name works, is unchanged.
"""

from __future__ import annotations

import pytest

from gingugu import prompt_hook, tool_hook
from tests.hook_remote_fixtures import WIRE, _prompt, _tool

SPACED = "/w/My Repo"


@pytest.fixture(autouse=True)
def no_fence(monkeypatch):
    monkeypatch.delenv("MEMORY_GRANT", raising=False)


def test_recall_leaves_out_a_name_the_brain_would_refuse(remote):
    _, install = remote
    brain = install({"/hook/recall": {"context": None, "ids": []}})
    prompt_hook.run({**_prompt(), "cwd": SPACED})
    assert brain.calls[0][1]["namespaces"] == ["crow"]


def test_tripwires_leave_out_a_name_the_brain_would_refuse(remote):
    _, install = remote
    brain = install({"/hook/tripwires": {"tripwires": [WIRE]}, "/hook/trip": {"ok": True}})
    tool_hook.run({**_tool(), "cwd": SPACED})
    assert brain.calls[0] == ("/hook/tripwires", {"namespaces": ["crow"]})
    assert brain.calls[1][1]["namespaces"] == ["crow"]


def test_local_mode_keeps_the_name():
    assert prompt_hook.namespaces_for(SPACED) == ["crow", "My Repo"]
