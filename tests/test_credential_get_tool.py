"""Tests for ``credential_get`` at the tool surface: redaction, reveal, into.

The vault-level behaviour is in ``test_credential_redact.py``. These pin what a
client actually receives, and that ``into`` is refused under `gingugu serve`,
where the path would name the server's disk rather than the caller's.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def make_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "cred.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "cred-test")
    monkeypatch.delenv("MEMORY_CREDENTIALS_ENABLED", raising=False)
    from gingugu.server import build_server

    return build_server


async def _store(server) -> None:
    fields = {
        "base_url": {"value": "https://x.atlassian.net", "is_secret": False},
        "api_token": {"value": "sk-secret-123"},
    }
    res = _payload(
        await server.call_tool(
            "credential_store", {"service_name": "jira", "fields": json.dumps(fields)}
        )
    )
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_tool_default_response_carries_no_secret(make_server) -> None:
    server = make_server()
    await _store(server)
    res = _payload(await server.call_tool("credential_get", {"service_name": "jira"}))
    assert res["ok"] is True
    assert res["service"]["fields"]["api_token"] == {"is_secret": True, "redacted": True}
    assert "sk-secret-123" not in json.dumps(res)


@pytest.mark.asyncio
async def test_tool_reveal_returns_the_value(make_server) -> None:
    server = make_server()
    await _store(server)
    res = _payload(
        await server.call_tool("credential_get", {"service_name": "jira", "reveal": True})
    )
    assert res["service"]["fields"]["api_token"]["value"] == "sk-secret-123"


@pytest.mark.asyncio
async def test_tool_into_writes_the_file_and_returns_no_value(make_server, tmp_path) -> None:
    server = make_server()
    await _store(server)
    target = tmp_path / "token"
    res = _payload(
        await server.call_tool(
            "credential_get",
            {"service_name": "jira", "fields": "api_token", "into": str(target)},
        )
    )
    assert res["ok"] is True
    assert res["written"] == str(target)
    assert target.read_bytes() == b"sk-secret-123"
    assert "sk-secret-123" not in json.dumps(res)


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [None, "api_token,base_url"])
async def test_tool_into_needs_exactly_one_field(make_server, tmp_path, fields) -> None:
    server = make_server()
    await _store(server)
    args = {"service_name": "jira", "into": str(tmp_path / "token")}
    if fields:
        args["fields"] = fields
    res = _payload(await server.call_tool("credential_get", args))
    assert res["ok"] is False
    assert "exactly one" in res["error"]
    assert not (tmp_path / "token").exists()


@pytest.mark.asyncio
async def test_tool_into_and_reveal_are_exclusive(make_server, tmp_path) -> None:
    server = make_server()
    await _store(server)
    res = _payload(
        await server.call_tool(
            "credential_get",
            {
                "service_name": "jira",
                "fields": "api_token",
                "into": str(tmp_path / "token"),
                "reveal": True,
            },
        )
    )
    assert res["ok"] is False
    assert "sk-secret-123" not in json.dumps(res)
    assert not (tmp_path / "token").exists()


@pytest.mark.asyncio
async def test_tool_into_refused_over_http(make_server, tmp_path) -> None:
    server = make_server(transport="http")
    await _store(server)
    target = tmp_path / "token"
    res = _payload(
        await server.call_tool(
            "credential_get",
            {"service_name": "jira", "fields": "api_token", "into": str(target)},
        )
    )
    assert res["ok"] is False
    assert "serve" in res["error"]
    assert not target.exists()


@pytest.mark.asyncio
async def test_tool_into_error_never_echoes_the_secret(make_server, tmp_path) -> None:
    server = make_server()
    await _store(server)
    res = _payload(
        await server.call_tool(
            "credential_get",
            {"service_name": "jira", "fields": "api_token", "into": "relative/path"},
        )
    )
    assert res["ok"] is False
    assert "sk-secret-123" not in json.dumps(res)
