"""The client half of TLS on the brain: pins, verification, and downgrade refusal.

``gingugu remote login`` fetches the brain's self-signed cert over SSH (the
forced command's ``cert`` verb) and pins it here, beside the machine setting,
one file per host and port. Every remote client - the stdio proxy, its token
trade, the hooks, ``remote status`` - verifies through ``verify_for``: against
the pin alone when there is one, the default trust store when there is not.

A pin also means "this brain speaks TLS", so plain http to a pinned host and
port is refused rather than sending the owner token in the clear. A pin that
does not parse fails closed: it is never read as "no pin".

Login settles all of this before it rotates the token (``prepare_login``): it
checks the live brain with the exact trust clients will use and writes the pin
first, so a cert that is stale or misses the host costs a retry, not a lockout.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from .serve_tls import fingerprint, parse_cert

__all__ = ["parse_cert"]  # re-exported: the client validates with the server's own rule

_UNSAFE = re.compile(r"[^A-Za-z0-9.-]")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_CLOCK_CODES = (9, 10)  # X509_V_ERR_CERT_NOT_YET_VALID, X509_V_ERR_CERT_HAS_EXPIRED
Runner = Callable[..., subprocess.CompletedProcess[str]]


def pin_dir() -> Path:
    from . import remote  # lazy: remote imports this module

    return remote.settings_path().parent / "remote-tls"


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "", parsed.port or _DEFAULT_PORTS.get(parsed.scheme, 0)


def pin_path(url: str) -> Path:
    host, port = _host_port(url)
    return pin_dir() / f"{_UNSAFE.sub('_', host)}_{port}.pem"


def https_of(url: str) -> str:
    """The same brain over https: same host and port, only the scheme changes."""
    return urlparse(url)._replace(scheme="https").geturl()


def load_pin(url: str) -> str | None:
    """The pinned cert, or None when there is none. A corrupt pin raises."""
    path = pin_path(url)
    try:
        text = path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the TLS pin {path}; run `gingugu remote login`") from exc
    try:
        return parse_cert(text)
    except ValueError:
        raise ValueError(f"the TLS pin {path} is corrupt; run `gingugu remote login`") from None


def save_pin(url: str, pem: str) -> str:
    """Pin ``pem`` for the brain at ``url``; returns its SHA-256 fingerprint."""
    pem = parse_cert(pem)
    path = pin_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pin.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(pem)
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
    return fingerprint(pem)


def clear_pin(url: str) -> None:
    try:
        pin_path(url).unlink()
    except FileNotFoundError:
        pass


def _context(pem: str | None) -> ssl.SSLContext:
    """The pin alone, or else the default trust httpx2 gives every client.

    httpx2's default is the OS trust store (via truststore), not OpenSSL's, so
    building it here keeps ``probe`` checking exactly what the clients check.
    """
    if pem:
        return ssl.create_default_context(cadata=pem)
    import httpx2

    return httpx2.create_ssl_context()


def verify_for(url: str) -> ssl.SSLContext | bool:
    """httpx2's ``verify`` for ``url``: the pin alone when there is one, else default trust."""
    pem = load_pin(url) if urlparse(url).scheme == "https" else None
    return True if pem is None else _context(pem)


def refuse_downgrade(url: str) -> None:
    """Plain http to a pinned brain is a misconfiguration: raise, never send.

    Loads the pin whatever the scheme, so a corrupt one fails at startup.
    """
    if load_pin(url) is not None and urlparse(url).scheme == "http":
        raise ValueError(f"this brain speaks TLS; use {https_of(url)}")


# --- why a handshake failed ------------------------------------------------------


def tls_failure(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """The certificate failure somewhere in ``exc``'s chain, if that is what it was."""
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 10:
        if isinstance(current, ssl.SSLCertVerificationError):
            return current
        reason = getattr(current, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return reason
        current = current.__cause__ or current.__context__
        seen += 1
    return None


def explain(url: str, err: ssl.SSLCertVerificationError) -> str:
    """What to do about a brain that failed TLS verification."""
    detail = err.verify_message or err.__class__.__name__
    if "mismatch" in detail.lower():  # "Hostname mismatch" or "IP address mismatch"
        host = urlparse(url).hostname
        return (
            f"TLS verification failed: the brain's certificate does not name {host}. "
            "On the brain, add it to MEMORY_SERVE_TLS_SAN, delete <db-dir>/tls, restart, "
            "then run `gingugu remote login` again"
        )
    if is_clock(err):
        return f"TLS verification failed for {url} ({detail}). Check this machine's clock"
    return (
        f"TLS verification failed for {url} ({detail}). If the brain's certificate "
        "changed, run `gingugu remote login` again; if login keeps offering this one, "
        "the brain now serves another (its own cert, or TLS off): delete <db-dir>/tls "
        "on the brain and log in again"
    )


def is_clock(err: ssl.SSLCertVerificationError) -> bool:
    """Not yet valid or expired: a clock problem as often as a cert one, so worth a retry."""
    return err.verify_code in _CLOCK_CODES


def probe(url: str, pem: str | None, *, timeout: float = 5) -> None:
    """GET ``/healthz`` over https trusting ``pem`` alone (default trust when None).

    Raises ValueError saying what is wrong. No token is sent.
    """
    try:
        with urllib.request.urlopen(  # noqa: S310 - https only, a fixed path
            f"{url}/healthz", timeout=timeout, context=_context(pem)
        ) as resp:
            if resp.status == 200:
                return
            raise ValueError(f"{url}/healthz answered HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"{url}/healthz answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        cert_err = tls_failure(exc)
        if cert_err is not None:
            raise ValueError(explain(url, cert_err)) from None
        reason = getattr(exc, "reason", exc)
        raise ValueError(
            f"cannot reach {url}/healthz over https ({reason.__class__.__name__}); "
            "is the brain running and serving https?"
        ) from None


def speaks_tls(url: str, *, timeout: float = 2) -> bool:
    """True when the port completes a TLS handshake; False on any failure."""
    host, port = _host_port(url)
    # No verification is safe here: the probe only proves the port speaks TLS and
    # sends nothing but a ClientHello - no request, no token.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return True
    except Exception:
        return False


def diagnose(url: str) -> str | None:
    """Why a brain cannot be reached, or None.

    https: this machine's trust fails. http: the port speaks TLS (the brain went TLS-only).
    """
    scheme = urlparse(url).scheme
    if scheme == "http":
        if not speaks_tls(url):
            return None
        host, port = _host_port(url)
        return (
            f"the brain at {host}:{port} speaks TLS, not plain http; "
            f"run `gingugu remote login {https_of(url)} --ssh USER@HOST`"
        )
    if scheme != "https":
        return None
    try:
        probe(url, load_pin(url), timeout=2)  # after the 15s budget, inside 30s connect
    except ValueError as exc:
        return str(exc)
    return None


# --- login ---------------------------------------------------------------------


def fetch_cert(run: Runner, ssh: list[str]) -> str | None:
    """Ask the brain's forced command for its cert: the PEM, or None when it has none.

    An older brain refuses the verb (exit 2), and an older brain has no TLS.
    Anything else that is not a clean answer raises ValueError.
    """
    try:
        proc = run([*ssh, "cert"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"ssh failed: {exc.__class__.__name__}") from None
    if proc.returncode == 2:
        return None
    if proc.returncode != 0:
        raise ValueError(f"ssh exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
    if not (proc.stdout or "").strip():
        return None
    try:
        return parse_cert(proc.stdout)
    except ValueError as exc:
        raise ValueError(f"the brain sent a bad certificate: {exc}") from None


def prepare_login(run: Runner, ssh: list[str], url: str) -> tuple[str, str | None]:
    """Settle TLS before the token is rotated: (URL to file it under, pin fingerprint).

    Every refusal raises ValueError with nothing rotated and no pin changed.
    """
    pem = fetch_cert(run, ssh)
    if pem is not None:
        url = https_of(url)
    elif urlparse(url).scheme == "http":
        refuse_downgrade(url)  # a pinned brain that now offers no cert: say so
        return url, None
    try:
        probe(url, pem)  # the live brain, with the exact trust clients will use
    except ValueError as exc:
        raise ValueError(f"{exc} (nothing was rotated)") from None
    if pem is None:
        clear_pin(url)  # a brought cert: default trust, never a stale pin
        return url, None
    return url, save_pin(url, pem)
