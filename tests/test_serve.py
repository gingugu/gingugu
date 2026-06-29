"""Tests for `gingugu serve` transport auth + the credentials tool-surface flag."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from gingugu.serve import BearerAuthMiddleware, _resolve_token

_CRED_TOOLS = {
    "credential_store",
    "credential_get",
    "credential_list",
    "credential_delete",
}


@pytest.fixture
def build(tmp_path, monkeypatch):
    """Factory that builds a server against a throwaway DB."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "serve.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "serve-test")

    def _make():
        from gingugu.server import build_server

        return build_server()

    return _make


# --- credentials tool-surface flag -----------------------------------------


@pytest.mark.asyncio
async def test_credentials_enabled_by_default(build, monkeypatch):
    monkeypatch.delenv("MEMORY_CREDENTIALS_ENABLED", raising=False)
    tools = {t.name for t in await build().list_tools()}
    assert _CRED_TOOLS.issubset(tools)


@pytest.mark.asyncio
async def test_credentials_disabled_hides_only_credential_tools(build, monkeypatch):
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")
    tools = {t.name for t in await build().list_tools()}
    assert not (_CRED_TOOLS & tools)
    assert "memory_store" in tools  # core surface untouched


# --- Bearer auth middleware -------------------------------------------------


def _wrapped_app(token: str) -> Starlette:
    async def ok(_request):
        return PlainTextResponse("secret")

    app = Starlette(routes=[Route("/mcp", ok, methods=["GET"])])
    app.add_middleware(BearerAuthMiddleware, token=token)
    return app


def test_auth_rejects_missing_token():
    assert TestClient(_wrapped_app("s3cret")).get("/mcp").status_code == 401


def test_auth_rejects_wrong_token():
    client = TestClient(_wrapped_app("s3cret"))
    resp = client.get("/mcp", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_auth_accepts_correct_token():
    client = TestClient(_wrapped_app("s3cret"))
    resp = client.get("/mcp", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200
    assert resp.text == "secret"


def test_health_exempt_from_auth():
    resp = TestClient(_wrapped_app("s3cret")).get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


# --- token resolution -------------------------------------------------------


def test_resolve_token_uses_configured(tmp_path):
    path = tmp_path / "serve_token"
    assert _resolve_token("preset-token", path) == "preset-token"
    assert not path.exists()  # explicit override is never persisted


def test_resolve_token_generates_and_persists(tmp_path):
    path = tmp_path / "serve_token"
    token = _resolve_token(None, path)
    assert isinstance(token, str)
    assert len(token) >= 32
    assert path.read_text(encoding="utf-8").strip() == token  # saved locally


def test_resolve_token_reuses_persisted(tmp_path):
    path = tmp_path / "serve_token"
    first = _resolve_token(None, path)
    second = _resolve_token(None, path)  # stable across restarts
    assert first == second
