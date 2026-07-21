"""Tests for `gingugu ui` — CLI dispatch, arg parsing, and static serving."""

from __future__ import annotations

import pytest

from gingugu import server, webui


def test_server_routes_ui_to_webui_main(monkeypatch):
    seen = {}

    def fake_ui_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(webui, "main", fake_ui_main)
    monkeypatch.setattr("sys.argv", ["gingugu", "ui", "--no-browser"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert seen["argv"] == ["--no-browser"]


# --- main() argument parsing / dispatch -------------------------------------


def _record_prod(calls):
    def _rec(host, port, open_browser):
        calls.update(host=host, port=port, ob=open_browser)

    return _rec


def test_main_prod_default(monkeypatch):
    calls = {}
    monkeypatch.setattr(webui, "serve_prod", _record_prod(calls))
    monkeypatch.setattr(webui, "serve_dev", lambda open_browser: calls.update(dev=True))
    assert webui.main([]) == 0
    assert calls == {"host": webui.HOST, "port": webui.PORT, "ob": True}


def test_main_no_browser_and_custom_port(monkeypatch):
    calls = {}
    monkeypatch.setattr(webui, "serve_prod", _record_prod(calls))
    webui.main(["--no-browser", "--port", "9999", "--host", "0.0.0.0"])
    assert calls == {"host": "0.0.0.0", "port": 9999, "ob": False}


def test_main_dev_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(webui, "serve_prod", lambda *a, **k: calls.update(prod=True))

    def fake_dev(open_browser):
        calls.update(dev=True, ob=open_browser)

    monkeypatch.setattr(webui, "serve_dev", fake_dev)
    webui.main(["--dev", "--no-browser"])
    assert calls == {"dev": True, "ob": False}


# --- serve_prod / serve_dev guards ------------------------------------------


def test_serve_prod_missing_dist_exits(monkeypatch):
    monkeypatch.setattr(webui, "find_dist", lambda: None)
    with pytest.raises(SystemExit) as exc:
        webui.serve_prod(webui.HOST, webui.PORT, open_browser=False)
    assert "not found" in str(exc.value)


def test_serve_dev_requires_repo_checkout(monkeypatch):
    monkeypatch.setattr(webui, "_repo_ui_dir", lambda: None)
    with pytest.raises(SystemExit) as exc:
        webui.serve_dev(open_browser=False)
    assert "repo root" in str(exc.value)


# --- static file resolution (security-critical) -----------------------------


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>root")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("in-root")  # sibling of dist below
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html>spa")
    (root / "assets").mkdir()
    (root / "assets" / "app.js").write_text("APP")
    return root


def test_resolve_serves_real_file(dist):
    assert webui.resolve_static_path(dist, "/assets/app.js") == (dist / "assets" / "app.js")


def test_resolve_root_serves_index(dist):
    assert webui.resolve_static_path(dist, "/") == dist / "index.html"


def test_resolve_unknown_route_falls_back_to_index(dist):
    # SPA client-side route with no file on disk -> index.html
    assert webui.resolve_static_path(dist, "/graph/some/deep/route") == dist / "index.html"


def test_resolve_query_string_stripped(dist):
    assert webui.resolve_static_path(dist, "/assets/app.js?v=abc") == dist / "assets" / "app.js"


@pytest.mark.parametrize(
    "attack",
    ["/../secret.txt", "/../../etc/passwd", "/assets/../../secret.txt"],
)
def test_resolve_blocks_traversal(dist, attack):
    # Escaping the dist root must never resolve to a file outside it.
    result = webui.resolve_static_path(dist, attack)
    assert result is None or result == dist / "index.html"
    if result is not None:
        assert result.resolve().is_relative_to(dist.resolve())
