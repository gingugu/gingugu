"""Scoped and owner bearer tokens for `gingugu serve` (the CLI is ``token_cli.py``).

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

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .grants import LEVELS, WRITE, Grant

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
        if Grant(name, namespaces).is_full:  # also validates levels and names
            raise ValueError(
                "'*=write' is full access - mint it explicitly with "
                "'token add NAME --owner', not as a scoped grant"
            )
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

    def add_owner(self, name: str, replace: bool = False) -> str:
        """Create a named full-access (owner) token; returns the plaintext once.

        One per machine, revocable on its own. ``replace`` rotates: an existing
        entry of that name is dropped in the same atomic write.
        """
        if not name or not name.strip():
            raise ValueError("token name must not be empty")
        entries = self._load()  # raises on a corrupt file; never overwrite it
        existing = [e for e in entries if e.get("name") == name]
        if existing:
            if not replace:
                raise ValueError(f"a token named {name!r} already exists")
            # Rotation replaces an owner token only: a scoped client's token is
            # never clobbered, nor its name turned into full access.
            if any(e.get("owner") is not True for e in existing):
                raise ValueError(f"{name!r} is a scoped token; revoke it before reusing the name")
            entries = [e for e in entries if e.get("name") != name]
        token = secrets.token_urlsafe(32)
        entries.append(
            {
                "name": name,
                "sha256": _hash(token),
                "owner": True,
                "namespaces": {},
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
                "owner": bool(e.get("owner") is True),
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
        if match.get("owner") is True:  # literal True only: "true", 1, "yes" do not count
            return Grant(str(match.get("name", "")), {"*": WRITE})
        try:
            grant = Grant(str(match.get("name", "")), dict(match.get("namespaces") or {}))
        except (ValueError, TypeError):
            return None
        # A scoped token never carries owner access, even if the file says so.
        return None if grant.is_full else grant
