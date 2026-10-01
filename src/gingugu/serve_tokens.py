"""Scoped bearer tokens for `gingugu serve`, and the `gingugu token` CLI.

Each token is bound to a name and a grant (``{namespace: read|write}``, see
``grants.py``). The store is one JSON file next to the memory DB. Only the
SHA-256 of each token is stored: the plaintext is shown once by
``gingugu token add`` and never written, logged, or recoverable afterwards.

The CLI and the running server are separate processes sharing that file, so
``resolve`` re-reads it whenever it changes on disk. Revoking a token takes
effect on the next request without restarting the server. Any problem reading
the file resolves to "no grant" - the store fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .grants import LEVELS, Grant

logger = logging.getLogger(__name__)

_VERSION = 1
_FileKey = tuple[int, int, int]  # (st_mtime_ns, st_size, st_ino)


def default_path() -> Path:
    """The token file, next to the memory DB."""
    return load_config().db_path.parent / "serve_tokens.json"


def parse_grant_spec(spec: str) -> dict[str, str]:
    """Parse ``"name=level,name=level"`` (name may be ``*``) into a grant map."""
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, level = part.partition("=")
        name, level = name.strip(), level.strip()
        if not sep:
            raise ValueError(f"expected name=level, got {part!r}")
        if not name:
            raise ValueError(f"empty namespace name in {part!r}")
        if level not in LEVELS:
            raise ValueError(f"level for {name!r} must be one of {', '.join(LEVELS)}")
        if name in out:
            raise ValueError(f"namespace {name!r} listed twice")
        out[name] = level
    if not out:
        raise ValueError("no namespace grants given")
    return out


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    """Hashed scoped tokens in a 0600 JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cache: tuple[_FileKey, list[dict[str, Any]]] | None = None

    # --- file access ---------------------------------------------------------

    def _key(self) -> _FileKey | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _load(self) -> list[dict[str, Any]]:
        """Parsed entries; [] if the file is missing; ValueError if corrupt."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ValueError(f"cannot read token file: {exc.__class__.__name__}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("token file is not valid JSON") from exc
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, list) or not all(isinstance(t, dict) for t in tokens):
            raise ValueError("token file has an unexpected shape")
        return tokens

    def _entries(self) -> list[dict[str, Any]]:
        """Entries via the (mtime, size, inode) cache; ValueError if corrupt."""
        key = self._key()
        if key is None:
            self._cache = None
            return []
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        entries = self._load()
        self._cache = (key, entries)
        return entries

    def _write(self, entries: list[dict[str, Any]]) -> None:
        """Atomically replace the file (temp file, 0600, ``os.replace``)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": _VERSION, "tokens": entries}, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".serve_tokens.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            try:
                os.chmod(tmp, 0o600)
            except OSError:  # pragma: no cover - platform-dependent (e.g. Windows)
                pass
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._cache = None

    # --- public API ----------------------------------------------------------

    def add(self, name: str, namespaces: dict[str, str]) -> str:
        """Create a token for ``name``; returns the plaintext, shown once."""
        if not name or not name.strip():
            raise ValueError("token name must not be empty")
        if not namespaces:
            raise ValueError("a token needs at least one namespace grant")
        Grant(name, namespaces)  # validates levels and names
        entries = self._load()  # raises on a corrupt file; never overwrite it
        if any(e.get("name") == name for e in entries):
            raise ValueError(f"a token named {name!r} already exists")
        token = secrets.token_urlsafe(32)
        entries.append(
            {
                "name": name,
                "sha256": _hash(token),
                "namespaces": dict(namespaces),
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        self._write(entries)
        return token

    def list(self) -> list[dict[str, Any]]:
        """Name, grant, and creation time of every token. Never hashes."""
        return [
            {
                "name": e.get("name"),
                "namespaces": e.get("namespaces", {}),
                "created_at": e.get("created_at"),
            }
            for e in self._load()
        ]

    def revoke(self, name: str) -> bool:
        """Delete the token called ``name``; False if there was none."""
        entries = self._load()
        kept = [e for e in entries if e.get("name") != name]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True

    def resolve(self, token: str) -> Grant | None:
        """The grant a presented token carries, or None (unknown / unreadable)."""
        if not token:
            return None
        try:
            entries = self._entries()
        except ValueError as exc:
            logger.warning("serve token file unusable, refusing all tokens: %s", exc)
            return None
        presented = _hash(token)
        match: dict[str, Any] | None = None
        for entry in entries:  # compare every entry; no early exit on a hit
            stored = entry.get("sha256")
            if isinstance(stored, str) and hmac.compare_digest(stored, presented):
                match = entry
        if match is None:
            return None
        try:
            return Grant(str(match.get("name", "")), dict(match.get("namespaces") or {}))
        except (ValueError, TypeError):
            return None


# --- CLI ---------------------------------------------------------------------

USAGE = """\
gingugu token - manage scoped bearer tokens for `gingugu serve`

Usage:
  gingugu token add NAME --ns SPEC   Create a token; it is printed once.
  gingugu token list                 Show token names and grants (never secrets).
  gingugu token revoke NAME          Delete a token; takes effect immediately.

SPEC is comma-separated name=level pairs, level read or write, name may be *:
  gingugu token add laptop-2 --ns gingugu=write,crow=read
"""


class _UsageError(Exception):
    """argparse rejected the arguments."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # type: ignore[override]
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="gingugu token", usage=USAGE, add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    add = sub.add_parser("add", add_help=False)
    add.add_argument("name")
    add.add_argument("--ns", required=True)
    sub.add_parser("list", add_help=False)
    revoke = sub.add_parser("revoke", add_help=False)
    revoke.add_argument("name")
    return parser


def _usage_error(message: str) -> int:
    print(f"gingugu token: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    """Entry point for ``gingugu token``; returns the process exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except _UsageError as exc:
        return _usage_error(str(exc))
    if args.help:
        print(USAGE)
        return 0
    if not args.cmd:
        return _usage_error("expected add, list, or revoke")

    store = TokenStore(default_path())
    try:
        if args.cmd == "add":
            try:
                grants = parse_grant_spec(args.ns)
            except ValueError as exc:
                return _usage_error(f"--ns: {exc}")
            token = store.add(args.name, grants)
            print(f"Token {args.name!r} created. It is shown once; store it now.", file=sys.stderr)
            print(token)
        elif args.cmd == "list":
            entries = store.list()
            if not entries:
                print("no tokens")
            for e in entries:
                spec = ",".join(f"{ns}={lvl}" for ns, lvl in e["namespaces"].items())
                print(f"{e['name']}  {spec}  {e['created_at']}")
        else:
            if not store.revoke(args.name):
                print(f"gingugu token: no token named {args.name!r}", file=sys.stderr)
                return 1
            print(f"Revoked {args.name!r}.")
    except (ValueError, OSError) as exc:
        print(f"gingugu token: {exc}", file=sys.stderr)
        return 1
    return 0
