"""Shared rig for the confirmation suites: a real server, a real client, a person."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from mcp import Client, types

from tests.test_embeddings import FakeEmbedder

MODES = ("legacy", "2026-07-28")


@dataclass
class Person:
    """Answers the dialog. ``None`` as the action means no elicitation capability."""

    action: str | None = "accept"
    confirm: bool = True
    asked: list[str] = field(default_factory=list)

    async def __call__(self, context, params):
        self.asked.append(params.message)
        content = {"confirm": self.confirm} if self.action == "accept" else None
        return types.ElicitResult(action=self.action, content=content)

    def client(self, server, mode: str) -> Client:
        callback = self if self.action is not None else None
        return Client(server, mode=mode, elicitation_callback=callback)


def payload(result) -> dict:
    return json.loads(result.content[0].text)


async def call(server, person: Person, mode: str, tool: str, args: dict) -> dict:
    async with person.client(server, mode) as client:
        return payload(await client.call_tool(tool, args))


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "confirm.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "confirm")
    monkeypatch.delenv("MEMORY_CONFIRM_DESTRUCTIVE", raising=False)
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.server import build_server

    return build_server()


async def stored(server, title: str = "a secret stored by mistake") -> str:
    """Store one memory without any dialog; return its id."""
    async with Client(server, mode="legacy") as client:
        out = payload(
            await client.call_tool(
                "memory_store", {"title": title, "content": "body", "type": "fact"}
            )
        )
    return out["memory"]["id"]


async def exists(server, memory_id: str) -> bool:
    async with Client(server, mode="legacy") as client:
        out = payload(await client.call_tool("memory_search", {"ids": memory_id}))
    return any(m["id"] == memory_id for m in out.get("memories", []))
