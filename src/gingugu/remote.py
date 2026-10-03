"""`gingugu remote`: which brain this machine (or one client) talks to.

Local is the default and needs no setting at all, so a fresh clone or a fresh
install is never pointed at someone's server. A machine setting lives in the
user data dir, never in a repo. A client can override it for itself with
MEMORY_REMOTE_URL (``off`` forces local). The token comes from the OS keychain
and is minted over SSH by the server's forced command - it is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import keyring

from .config import _default_db_path
from .remote_args import USAGE, _UsageError, build_parser

KEYRING_SERVICE = "gingugu-remote"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class RemoteTarget:
    url: str
    source: str  # "env" or "machine"


def settings_path() -> Path:
    """The machine setting. Ignores MEMORY_DB_PATH on purpose."""
    return _default_db_path().parent / "remote.json"


def normalize_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"not an http(s) URL: {raw!r}")
    return url


# --- keychain ----------------------------------------------------------------


def _kr_get(url: str) -> str | None:
    # None means "no token"; a broken keychain raises, so it is never reported
    # as a missing token.
    return keyring.get_password(KEYRING_SERVICE, url)


def _kr_set(url: str, token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, url, token)


def _kr_delete(url: str) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, url)
    except Exception:
        pass


def token_for(url: str) -> str | None:
    return _kr_get(normalize_url(url))


# --- state -------------------------------------------------------------------


def active() -> RemoteTarget | None:
    """The brain in force. A bad URL or file raises: never a silent local fallback."""
    env = os.environ.get("MEMORY_REMOTE_URL")
    if env is not None:
        if env.strip().lower() == "off":
            return None
        return RemoteTarget(normalize_url(env), "env")
    path = settings_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.__class__.__name__}") from exc
    try:
        data = json.loads(raw)
        url = data["url"]
        if not isinstance(url, str):
            raise TypeError
        return RemoteTarget(normalize_url(url), "machine")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{path} is corrupt; run `gingugu remote off`") from exc


def _write_setting(url: str) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".remote.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"url": url}, fh)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - platform-dependent
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, **kwargs)  # noqa: S603


def _reachable(url: str, token: str | None) -> bool:
    """GET /healthz. The token is deliberately not sent."""
    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=3) as resp:  # noqa: S310
            return bool(resp.status == 200)
    except Exception:
        return False


# --- CLI ---------------------------------------------------------------------


def _usage_error(message: str) -> int:
    print(f"gingugu remote: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


def _fail(message: str) -> int:
    print(f"gingugu remote: {message}", file=sys.stderr)
    return 1


def _status() -> int:
    target = active()
    if target is None:
        print("local")
        return 0
    print(f"remote: {target.url}")
    print(f"source: {target.source}")
    try:
        token = token_for(target.url)
        print(f"token: {'present' if token else 'missing'}")
    except Exception as exc:  # noqa: BLE001 - any keyring backend failure
        token = None
        print(f"token: keychain error ({exc.__class__.__name__})")
    print(f"reachable: {'yes' if _reachable(target.url, token) else 'no'}")
    return 0


def _on(raw: str) -> int:
    url = normalize_url(raw)
    try:
        token = token_for(url)
    except Exception as exc:  # noqa: BLE001 - any keyring backend failure
        return _fail(f"cannot read the keychain: {exc.__class__.__name__}: {exc}")
    if not token:
        return _fail(f"no token for {url}; run `gingugu remote login {url} --ssh USER@HOST`")
    _write_setting(url)
    print(f"remote: {url}")
    parsed = urlparse(url)
    if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK:
        print(
            "gingugu remote: warning - plain http: the token crosses the network unencrypted",
            file=sys.stderr,
        )
    return 0


def _off() -> int:
    try:
        settings_path().unlink()
    except FileNotFoundError:
        pass
    print("local")
    return 0


def _default_name() -> str:
    return re.sub(r"[^a-z0-9._-]", "-", socket.gethostname().split(".")[0].lower())


def _login(args: argparse.Namespace) -> int:
    url = normalize_url(args.url)
    name = args.name or _default_name()
    if not _NAME_RE.fullmatch(name):
        return _usage_error(f"invalid --name {name!r}")
    if args.ssh.startswith("-"):
        return _usage_error(f"--ssh must be USER@HOST, not an option: {args.ssh!r}")
    key = args.key or str(Path.home() / ".ssh" / "gingugu_mint")
    argv = [
        "ssh",
        "-F",
        "/dev/null",  # ignore ~/.ssh/config: a Host * IdentityFile would bypass the mint key
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=accept-new",  # a new machine's first login has no known_hosts entry
        args.ssh,
        "mint",
        name,
    ]
    try:
        proc = _run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return _fail(f"ssh failed: {exc.__class__.__name__}")
    lines = proc.stdout.splitlines()
    if proc.returncode != 0:
        # stdout may hold a token; report only the exit code and ssh's stderr.
        return _fail(f"ssh exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
    if len(lines) != 1 or not _TOKEN_RE.fullmatch(lines[0]):
        return _fail("ssh succeeded but did not return a token")
    try:
        _kr_set(url, lines[0])
    except Exception as exc:  # noqa: BLE001 - any keyring backend failure
        return _fail(
            f"minted, but could not store the token in the keychain "
            f"({exc.__class__.__name__}: {exc}). The server already rotated {name!r}; "
            "rerun login once the keychain works"
        )
    print(f"logged in to {url} as {name}")
    return 0


def main(argv: list[str]) -> int:
    """Entry point for ``gingugu remote``; returns the process exit code."""
    try:
        args = build_parser().parse_args(argv)
    except _UsageError as exc:
        return _usage_error(str(exc))
    if args.help:
        print(USAGE)
        return 0
    if not args.cmd:
        return _usage_error("expected status, on, off, or login")
    try:
        if args.cmd == "status":
            return _status()
        if args.cmd == "on":
            return _on(args.url)
        if args.cmd == "off":
            return _off()
        return _login(args)
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    except Exception as exc:  # keyring backend errors
        return _fail(f"{exc.__class__.__name__}")
