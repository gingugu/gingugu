"""`gingugu ui` against a remote brain: forward ``/api/*`` to ``/ui/api/*``.

The keychain token is sent as a Bearer header and goes no further - not into
the response the browser sees, not into an error, not into a log line.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from urllib.parse import urlparse

import httpx2

from . import remote, remote_tls
from .config import load_config
from .remote import RemoteTarget

logger = logging.getLogger(__name__)

TIMEOUT_S = 30.0

_embedder = None
_embedder_lock = threading.Lock()


def local_embedder():
    """The configured embedding provider, built once per process on first use."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            from .embeddings import build_provider

            cfg = load_config()
            _embedder = build_provider(
                enabled=cfg.embeddings_enabled,
                model_name=cfg.embeddings_model,
                backend=cfg.embeddings_backend,
                ollama_host=cfg.embeddings_ollama_host,
                ollama_model=cfg.embeddings_ollama_model,
            )
        return _embedder


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_is_loopback(header: str | None) -> bool:
    """True when a request's ``Host`` names this machine.

    Binding loopback stops other machines, not other web pages: a page can
    rebind its own hostname to 127.0.0.1, and the browser then sends that
    hostname here as ``Host``. Refusing any non-loopback name closes that.
    """
    if not header:
        return False
    host = header.strip()
    if host.startswith("["):  # [::1]:port
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return is_loopback(host)


def netloc(target: RemoteTarget) -> str:
    return urlparse(target.url).netloc


def forward(target: RemoteTarget, rest: str, query: str) -> tuple[int, dict]:
    """GET the brain's ``/ui/api/<rest>``; relay its status and JSON body."""
    try:
        token = remote.token_for(target.url)
    except Exception as exc:  # noqa: BLE001 - a broken keychain is a 502, not a crash
        return 502, {"ok": False, "error": f"cannot read the keychain: {exc.__class__.__name__}"}
    if not token:
        return 502, {
            "ok": False,
            "error": f"no token for {target.url} in the keychain - run `gingugu remote login`",
        }
    url = f"{target.url}/ui/api/{rest}" + (f"?{query}" if query else "")
    try:
        verify = remote_tls.verify_for(target.url)
        # Never follow a redirect: it would carry the owner token somewhere else.
        with httpx2.Client(timeout=TIMEOUT_S, verify=verify, follow_redirects=False) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("brain unreachable: %s", exc.__class__.__name__)
        return 502, {"ok": False, "error": f"brain unreachable: {exc.__class__.__name__}"}
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return 502, {"ok": False, "error": f"brain answered {response.status_code} without JSON"}
    return response.status_code, body
