"""``gingugu ui`` - launch the Memory Explorer web UI.

Two modes, one command:

* **prod** (default): a single process serves the pre-built React bundle
  *and* the ``/api/export`` endpoint on one port. The frontend fetches
  ``/api/export`` relative, so serving both from one origin needs no CORS
  and no Node at runtime. Assets are the ones bundled into the wheel
  (``gingugu/_ui_dist``); in a repo checkout we fall back to ``ui/dist``.
* **dev** (``--dev``): runs the API backend and spawns the Vite dev server
  (hot reload) the way the two-terminal workflow used to. Repo checkout and
  Node required; Vite proxies ``/api`` to the backend on port 5174.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .config import load_config
from .database import Database
from .portability import export_data

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 5174

# Fixed ports for --dev: Vite serves the frontend on 5173 and proxies /api to
# the backend on 5174 (see ui/vite.config.ts). --host/--port are prod-only.
_DEV_FRONTEND_URL = "http://localhost:5173"
_DEV_BACKEND_PORT = 5174

# Only the Vite dev server may read the API cross-origin. In prod the UI is
# same-origin so no Origin header ever matches - this matters for --dev only.
# A wildcard would let any website in the browser exfiltrate the memory DB.
ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}


def _repo_ui_dir() -> Path | None:
    """Return ``<repo>/ui`` when running from a source checkout, else None.

    ``__file__`` is ``<repo>/src/gingugu/webui.py`` → parents[2] is the repo.
    """
    ui = Path(__file__).resolve().parents[2] / "ui"
    return ui if ui.is_dir() else None


def find_dist() -> Path | None:
    """Locate the built UI, preferring the wheel bundle over the dev checkout."""
    packaged = Path(__file__).resolve().parent / "_ui_dist"
    if (packaged / "index.html").is_file():
        return packaged
    ui_dir = _repo_ui_dir()
    if ui_dir is not None and (ui_dir / "dist" / "index.html").is_file():
        return ui_dir / "dist"
    return None


def resolve_static_path(dist: Path, url_path: str) -> Path | None:
    """Map a request path to a file inside ``dist`` (SPA fallback to index.html).

    Returns None if the path escapes ``dist`` (traversal) or nothing matches -
    the caller answers 404 either way, never confirming what's outside the root.
    """
    rel = url_path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
    root = dist.resolve()
    try:
        resolved = (root / rel).resolve()
        resolved.relative_to(root)  # ValueError if rel escaped the root
    except (ValueError, OSError):
        return None
    if resolved.is_file():
        return resolved
    index = root / "index.html"
    return index if index.is_file() else None


class Handler(BaseHTTPRequestHandler):
    db: Database | None = None
    dist_dir: Path | None = None
    serve_static: bool = False

    def _get_conn(self):
        if Handler.db is None:
            Handler.db = Database(load_config().db_path)
        return Handler.db.connect()

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        ctype, _ = mimetypes.guess_type(str(path))
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_export(self) -> None:
        try:
            payload = export_data(self._get_conn())
        except Exception as exc:
            logger.exception("Export failed")
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, payload)

    def _serve_static(self) -> None:
        target = resolve_static_path(Handler.dist_dir, self.path) if Handler.dist_dir else None
        if target is None:
            self.send_response(404)
            self.end_headers()
            return
        self._send_file(target)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/api/export":
            self._serve_export()
        elif Handler.serve_static:
            self._serve_static()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        logger.info(format, *args)


def _build_server(host: str, port: int, *, serve_static: bool, dist: Path | None) -> HTTPServer:
    Handler.dist_dir = dist
    Handler.serve_static = serve_static
    return HTTPServer((host, port), Handler)


def serve_prod(host: str, port: int, open_browser: bool) -> None:
    """Serve the bundled UI + /api on one port (no Node required)."""
    dist = find_dist()
    if dist is None:
        raise SystemExit(
            "gingugu ui: built UI assets not found.\n"
            "  Build them with `npm run build` in ui/, or run `gingugu ui --dev`."
        )
    httpd = _build_server(host, port, serve_static=True, dist=dist)
    url = f"http://{host}:{port}"
    logger.info("Memory Explorer serving at %s (assets: %s)", url, dist)
    print(f"Memory Explorer -> {url}  (Ctrl-C to stop)", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        httpd.server_close()


def serve_dev(open_browser: bool) -> None:
    """Run the API backend and spawn the Vite dev server (hot reload)."""
    import subprocess

    ui_dir = _repo_ui_dir()
    if ui_dir is None or not (ui_dir / "package.json").is_file():
        raise SystemExit("gingugu ui --dev must run from the gingugu repo root (no ui/ found).")
    if not (ui_dir / "node_modules").is_dir():
        raise SystemExit("gingugu ui --dev: run `npm install` in ui/ first (node_modules missing).")

    vite = subprocess.Popen(["npm", "run", "dev"], cwd=str(ui_dir))  # noqa: S603,S607
    httpd = _build_server(HOST, _DEV_BACKEND_PORT, serve_static=False, dist=None)
    logger.info("Dev API :%d/api, Vite %s", _DEV_BACKEND_PORT, _DEV_FRONTEND_URL)
    print(f"Memory Explorer (dev) -> {_DEV_FRONTEND_URL}  (Ctrl-C to stop)", file=sys.stderr)
    if open_browser:
        webbrowser.open(_DEV_FRONTEND_URL)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        vite.terminate()
        try:
            vite.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - teardown race
            vite.kill()
        httpd.server_close()


def main(argv: list[str]) -> int:
    """Console entry point for ``gingugu ui``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="gingugu ui",
        description="Launch the Memory Explorer web UI (single process, no Node needed).",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: API backend + Vite hot-reload server (repo checkout + Node required).",
    )
    parser.add_argument("--host", default=HOST, help=f"Host to bind, prod only (default {HOST}).")
    parser.add_argument("--port", type=int, default=PORT, help="Port to bind (prod only).")
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser.")
    args = parser.parse_args(argv)

    open_browser = not args.no_browser
    if args.dev:
        serve_dev(open_browser=open_browser)
    else:
        serve_prod(args.host, args.port, open_browser)
    return 0
