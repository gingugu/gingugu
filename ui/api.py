"""Lightweight API server that reads the Gingugu SQLite DB and serves live data.

Run with: uv run python ui/api.py
Serves on http://127.0.0.1:5174/api/export
"""

from __future__ import annotations

import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from gingugu.config import load_config
from gingugu.database import Database
from gingugu.portability import export_data

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 5174

# Only the Memory Explorer dev server may read this API cross-origin. A
# wildcard here would let ANY website open in the browser exfiltrate the
# entire memory DB via fetch() — localhost binding does not prevent that.
ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}


class Handler(BaseHTTPRequestHandler):
    db: Database | None = None

    def _get_conn(self):
        if Handler.db is None:
            config = load_config()
            Handler.db = Database(config.db_path)
        return Handler.db.connect()

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/export":
            try:
                conn = self._get_conn()
                payload = export_data(conn)
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                logger.exception("Export failed")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        logger.info(format, *args)


def main():
    server = HTTPServer((HOST, PORT), Handler)
    logger.info("Memory API serving on http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
