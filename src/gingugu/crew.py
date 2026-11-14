"""The crew link: a local-brain machine's line to a shared crew brain.

A crew mate keeps its own local brain and talks to a shared brain (the crew's)
only through a scoped token. The link names that brain: a machine setting,
``crew.json`` beside ``remote.json`` in the user data dir, or
``MEMORY_CREW_URL`` for one client (``off`` unlinks). Unlinked is the default.
The token sits in the keychain under the brain's URL, where ``gingugu remote``
keeps tokens, so the crew MCP server - a proxy with ``MEMORY_REMOTE_URL`` set
to the same URL - finds it unchanged.

The link is only read in local mode: a machine whose whole brain is remote
already talks to that brain directly.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import remote, remote_tls
from .config import _default_db_path
from .remote import RemoteTarget, normalize_url

ENV = "MEMORY_CREW_URL"


def settings_path() -> Path:
    """The machine's crew link. Ignores MEMORY_DB_PATH on purpose, like remote.json."""
    return _default_db_path().parent / "crew.json"


def active() -> RemoteTarget | None:
    """The linked crew brain, or None. A bad URL or file raises."""
    env = os.environ.get(ENV)
    if env is not None:
        if env.strip().lower() == "off":
            return None
        target = RemoteTarget(normalize_url(env), "env")
    else:
        path = settings_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            url = json.loads(raw)["url"]
            if not isinstance(url, str):
                raise TypeError
            target = RemoteTarget(normalize_url(url), "machine")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path} is corrupt; run `gingugu crew leave`") from exc
    remote_tls.refuse_downgrade(target.url)
    _require_tls(target.url)
    return target


def _require_tls(url: str) -> None:
    """The scoped token rides every prompt's mail check: never in the clear."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"a crew link must be https (got {url}); see `gingugu remote login`")


def link(url: str) -> Path:
    """Write the machine's crew link, atomically, owner-only."""
    url = normalize_url(url)
    _require_tls(url)
    path = settings_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".crew.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"url": url}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def unlink() -> bool:
    """Remove the machine's crew link; True when there was one."""
    try:
        settings_path().unlink()
    except FileNotFoundError:
        return False
    return True


def token(target: RemoteTarget) -> str | None:
    """The scoped token for the crew brain, from the keychain."""
    return remote.token_for(target.url)
