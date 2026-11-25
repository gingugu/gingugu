"""The proxy's ``POST /token/derive`` call: trade the owner token for a session token.

Split from `proxy_session` to keep each file small. Never puts a token into an
exception or a log line.
"""

from __future__ import annotations

import httpx2

from .proxy_session import DeriveTransient, ProxyLost
from .remote_tls import explain, is_clock, tls_failure, verify_for


async def derive(
    url: str,
    owner_token: str,
    *,
    grant: str | None,
    home: str | None,
    ttl: int,
    name: str | None,
    replaces: str | None = None,
) -> tuple[str, int]:
    """Trade the owner token for a session token: ``(token, expires_in)``.

    ``replaces`` is the session token being renewed: the brain drops it as it
    mints the new one, so a renewing proxy never holds two live tokens.
    """
    fields = {"grant": grant, "home": home, "ttl": ttl, "name": name, "replaces": replaces}
    body = {key: value for key, value in fields.items() if value is not None}
    try:
        verify = verify_for(url)
    except ValueError as exc:  # a corrupt pin: refuse, never fall back
        raise ProxyLost(str(exc)) from None
    try:
        async with httpx2.AsyncClient(timeout=10, verify=verify) as client:
            resp = await client.post(
                f"{url}/token/derive",
                json=body,
                headers={"Authorization": f"Bearer {owner_token}"},
            )
    except httpx2.HTTPError as exc:
        cert_err = tls_failure(exc)
        if cert_err is not None and not is_clock(cert_err):
            # Not transient: retrying would hide it as "reconnecting".
            raise ProxyLost(explain(url, cert_err)) from None
        raise DeriveTransient(type(exc).__name__) from None
    status = resp.status_code
    if status in (401, 403):
        raise ProxyLost(
            f"remote brain {url} refused the machine token (HTTP {status}). "
            f"Run: gingugu remote login {url}"
        )
    if status >= 500 or status == 429:  # 429: the brain's live-token cap; it drains
        raise DeriveTransient(f"HTTP {status}")
    if status != 200:
        try:
            detail = str(resp.json().get("error", ""))
        except (ValueError, AttributeError):
            detail = ""
        raise ProxyLost(f"remote brain rejected the session request (HTTP {status}): {detail}")
    try:
        data = resp.json()
        return str(data["token"]), int(data["expires_in"])
    except (ValueError, KeyError, TypeError):
        raise DeriveTransient("malformed derive reply") from None
