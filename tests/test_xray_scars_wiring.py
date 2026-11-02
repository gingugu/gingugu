"""X3 scar tissue reaches the agent where the file's X-ray already does.

A file's first ``Read`` brings its neighbourhood, and now what it has cost
before; a blind edit's deny carries the same lines. An informed edit adds no
friction: its scars arrived with the read. A leaf file nothing imports has no
neighbourhood, but its scars still speak.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_hook, read_hook, scars
from tests.test_xray_edit_hook import BASIC, _payload, _repo

LINES = ["Scar tissue: 1 memory names this file:", "- bug 2026-10-01: core blew up [m-1]"]


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def scarred(monkeypatch):
    asked = []

    def for_file(root, rel, files):
        asked.append(rel)
        return list(LINES) if rel in ("pkg/core.py", "pkg/leaf.py") else []

    monkeypatch.setattr(scars, "for_file", for_file)
    return asked


def _read(repo, rel, session="cc-1"):
    out = _payload(repo, rel, tool="Read", session=session)
    out["hook_event_name"] = "PostToolUse"
    return out


def test_a_read_brings_the_scars_after_the_neighbourhood(repo, state, scarred):
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert "GINGUGU X-RAY: pkg/core.py" in text
    assert text.index("GINGUGU X-RAY: pkg/core.py") < text.index("Scar tissue")
    assert "core blew up" in text


def test_a_leaf_with_no_neighbourhood_still_shows_its_scars(repo, state, scarred):
    read_hook.check(_read(repo, "pkg/a.py"), state)  # the map, spent
    text = read_hook.check(_read(repo, "pkg/leaf.py"), state)
    assert text is not None
    assert "Scar tissue" in text
    assert "GINGUGU X-RAY: pkg/leaf.py" in text  # the header names the file


def test_scars_come_once_per_file_per_session(repo, state, scarred):
    read_hook.check(_read(repo, "pkg/core.py"), state)
    assert read_hook.check(_read(repo, "pkg/core.py"), state) is None
    assert scarred == ["pkg/core.py"]


def test_a_file_with_no_scars_reads_as_before(repo, state, scarred):
    text = read_hook.check(_read(repo, "pkg/a.py"), state)
    assert "Scar tissue" not in text


def test_a_blind_edit_deny_carries_the_scars(repo, state, scarred):
    reason = edit_hook.check(_payload(repo, "pkg/core.py"), state)
    assert "is imported directly by" in reason
    assert "core blew up" in reason
    assert reason.index("core blew up") < reason.index("re-issue the edit")


def test_an_edit_with_no_dependents_is_not_denied_for_scars(repo, state, scarred):
    assert edit_hook.check(_payload(repo, "pkg/leaf.py"), state) is None


def test_scars_failing_never_costs_the_neighbourhood(repo, state, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("brain on fire")

    monkeypatch.setattr(scars, "fetch", boom)
    text = read_hook.check(_read(repo, "pkg/core.py"), state)
    assert "GINGUGU X-RAY: pkg/core.py" in text
    assert "Scar tissue" not in text
