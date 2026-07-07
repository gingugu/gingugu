"""metadata accepts a dict (HTTP transports deliver JSON objects as dicts)."""

from __future__ import annotations

import json

import pytest

from gingugu.handlers.helpers import _coerce_metadata


def test_coerce_dict_to_json_string():
    assert _coerce_metadata({"a": 1}) == '{"a": 1}'


def test_coerce_list_to_json_string():
    assert _coerce_metadata([1, 2]) == "[1, 2]"


def test_coerce_passes_string_through():
    assert _coerce_metadata('{"a": 1}') == '{"a": 1}'


def test_coerce_passes_none_and_empty():
    assert _coerce_metadata(None) is None
    assert _coerce_metadata("") == ""  # caller convention: clears metadata


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "meta.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "meta")
    from gingugu.server import build_server

    return build_server()


@pytest.mark.asyncio
async def test_memory_store_accepts_dict_metadata(server):
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "provenance demo",
                "title": "p",
                "type": "fact",
                "confidence": "verified",
                "metadata": {"promoted_from": {"id": "src-1"}},
            },
        )
    )
    assert stored["ok"] is True
    export = _payload(await server.call_tool("memory_export", {"namespace": "meta"}))
    mem = export["export"]["memories"][0]
    assert json.loads(mem["metadata"]) == {"promoted_from": {"id": "src-1"}}
