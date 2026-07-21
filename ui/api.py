"""Thin shim: run the Memory Explorer API backend on :5174.

The serving logic now lives in the package (``gingugu.webui``) so it can ship
in the wheel and back `gingugu ui`. This file stays for the documented dev
workflow: `uv run python ui/api.py` (backend) + `cd ui && npm run dev` (Vite).
Prefer `gingugu ui --dev`, which starts both for you.
"""

from __future__ import annotations

from gingugu import webui


def main() -> None:
    # API-only backend (Vite serves the static assets and proxies /api here).
    httpd = webui._build_server(webui.HOST, webui.PORT, serve_static=False, dist=None)
    webui.logger.info("Memory API serving on http://%s:%d", webui.HOST, webui.PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    main()
