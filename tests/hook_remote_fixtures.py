"""Shared rig for the remote-mode hook suites: remote mode on, and a stand-in
for ``hook_remote.post`` that records every call and answers from a table."""

from __future__ import annotations

import pytest

URL = "http://brain.test:8765"
PROMPT = "how should the proxy reconnect after the brain restarts overnight"
WIRE = {
    "id": "t1",
    "memory_id": "m-merge",
    "tool_pattern": "Bash",
    "input_pattern": r"gh pr merge.*--delete-branch",
    "title": "Never --delete-branch a stack parent",
    "summary": "Retarget the children first.",
    "namespace": "crow",
    "type": "preference",
}


class Brain:
    """Stands in for ``hook_remote.post``: canned replies, every call recorded."""

    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, target, path, body, *, timeout):
        assert target.url == URL
        assert timeout > 0
        self.calls.append((path, body))
        reply = self.replies.get(path)
        return reply(body) if callable(reply) else reply

    def paths(self) -> list[str]:
        return [p for p, _ in self.calls]


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """Remote mode on; the local DB path points at a file that does not exist."""
    db = tmp_path / "data" / "memories.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.delenv("MEMORY_PERSONA", raising=False)
    monkeypatch.delenv("MEMORY_TRIPWIRES", raising=False)

    def install(replies: dict) -> Brain:
        from gingugu import hook_remote

        brain = Brain(replies)
        monkeypatch.setattr(hook_remote, "post", brain)
        return brain

    return db, install


def _no_local_encoder(*args, **kwargs):
    raise AssertionError("remote mode must not load the local encoder")


def _prompt(prompt=PROMPT, session="cc-1"):
    return {"prompt": prompt, "session_id": session, "cwd": "/w/gingugu"}


def _tool(command="gh pr merge 9 --delete-branch", session="cc-1"):
    return {
        "session_id": session,
        "cwd": "/w/gingugu",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
