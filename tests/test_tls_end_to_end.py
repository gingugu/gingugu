"""Every remote client reaches a TLS brain through its pin, and nothing else.

A real `gingugu serve` app behind real TLS on a loopback socket, with the
self-signed cert `gingugu serve` generates. Each of the four ways a client
talks to the brain - the stdio proxy, the token trade, the hooks and the
`remote status` health check - must verify against the pinned cert, and must
refuse a brain whose cert is not the pinned one.
"""

from __future__ import annotations

import pytest

from gingugu import hook_remote, remote, remote_tls, serve_tls
from gingugu.proxy_session import DeriveTransient, ProxyLost, derive
from gingugu.remote import RemoteTarget
from tests.proxy_fixtures import free_port, make_brain, payload, serving, through_proxy


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


def _cert(tmp_path, name: str) -> tuple[tuple[str, str], str]:
    cert, key = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
    serve_tls.generate(cert, key, ["localhost", "127.0.0.1"])
    return (str(cert), str(key)), cert.read_text(encoding="ascii")


@pytest.fixture
def tls(tmp_path):
    return _cert(tmp_path, "brain")


@pytest.fixture
def impostor(tmp_path):
    return _cert(tmp_path, "impostor")[1]


async def test_proxy_round_trip_over_tls_with_the_pin(brain, tls):
    files, pem = tls
    async with serving(brain.app(), tls=files) as served:
        remote_tls.save_pin(served.url, pem)
        async with through_proxy(served.url, brain.machine, home="gingugu") as (session, _):
            await session.initialize()
            stored = payload(
                await session.call_tool(
                    "memory_store",
                    {"title": "sealed note", "content": "brass porthole cipher", "type": "fact"},
                )
            )
    assert served.url.startswith("https://")
    assert stored["ok"] and stored["namespace"] == "gingugu"


async def test_token_trade_uses_the_pin(brain, tls):
    files, pem = tls
    async with serving(brain.app(), tls=files) as served:
        remote_tls.save_pin(served.url, pem)
        token, expires = await derive(
            served.url, brain.machine, grant=None, home=None, ttl=300, name=None
        )
    assert token and expires == 300


@pytest.mark.parametrize("pinned", ["none", "impostor"])
async def test_token_trade_refuses_an_unpinned_cert_and_says_why(brain, tls, impostor, pinned):
    # A cert mismatch is not transient: retrying would hide it behind "reconnecting".
    files, _ = tls
    async with serving(brain.app(), tls=files) as served:
        if pinned == "impostor":
            remote_tls.save_pin(served.url, impostor)
        with pytest.raises(ProxyLost, match="gingugu remote login"):
            await derive(served.url, brain.machine, grant=None, home=None, ttl=300, name=None)


async def test_token_trade_to_a_down_brain_is_still_transient(brain):
    url = f"https://127.0.0.1:{free_port()}"
    with pytest.raises(DeriveTransient):
        await derive(url, "t", grant=None, home=None, ttl=300, name=None)


# --- login's live check ------------------------------------------------------------


async def test_probe_passes_the_pinned_cert(brain, tls):
    files, pem = tls
    async with serving(brain.app(), tls=files) as served:
        await _to_thread(remote_tls.probe, served.url, pem)


@pytest.mark.parametrize("which", ["impostor", "default-trust"])
async def test_probe_refuses_a_cert_that_is_not_the_one_offered(brain, tls, impostor, which):
    files, _ = tls
    trust = None if which == "default-trust" else impostor
    async with serving(brain.app(), tls=files) as served:
        with pytest.raises(ValueError, match="TLS verification"):
            await _to_thread(remote_tls.probe, served.url, trust)


async def test_probe_names_the_fix_for_a_cert_that_misses_the_host(brain, tmp_path):
    cert, other = tmp_path / "other.pem", tmp_path / "other.k"
    serve_tls.generate(cert, other, ["brain.example"])
    async with serving(brain.app(), tls=(str(cert), str(other))) as served:
        with pytest.raises(ValueError, match="MEMORY_SERVE_TLS_SAN"):
            await _to_thread(remote_tls.probe, served.url, cert.read_text(encoding="ascii"))


async def test_probe_of_a_plain_http_listener_fails(brain, tls):
    _, pem = tls
    async with serving(brain.app()) as served:
        with pytest.raises(ValueError, match="https"):
            await _to_thread(remote_tls.probe, remote_tls.https_of(served.url), pem)


async def test_preflight_says_why_a_pinned_brain_is_refused(brain, tls, impostor, monkeypatch):
    from gingugu import proxy_preflight

    files, _ = tls
    monkeypatch.setattr(proxy_preflight, "REACH_BUDGET_S", 0)
    monkeypatch.setattr(remote, "token_for", lambda _url: brain.machine)
    async with serving(brain.app(), tls=files) as served:
        remote_tls.save_pin(served.url, impostor)
        target = RemoteTarget(served.url, "env")
        with pytest.raises(proxy_preflight.ProxyRefused, match="gingugu remote login"):
            await _to_thread(_preflight, proxy_preflight, target)


def _preflight(proxy_preflight, target):
    return proxy_preflight.preflight(target, grant=None, credentials_enabled=False)


def _hook_post(url: str, machine: str, monkeypatch):
    monkeypatch.setattr(remote, "token_for", lambda _url: machine)
    return hook_remote.post(RemoteTarget(url, "env"), "/token/derive", {"ttl": 300}, timeout=5)


async def test_hooks_use_the_pin(brain, tls, monkeypatch):
    files, pem = tls
    async with serving(brain.app(), tls=files) as served:
        remote_tls.save_pin(served.url, pem)
        reply = await _to_thread(_hook_post, served.url, brain.machine, monkeypatch)
    assert reply is not None and reply["expires_in"] == 300


@pytest.mark.parametrize("pinned", ["none", "impostor"])
async def test_hooks_stay_quiet_on_an_unpinned_cert(brain, tls, impostor, monkeypatch, pinned):
    files, _ = tls
    async with serving(brain.app(), tls=files) as served:
        if pinned == "impostor":
            remote_tls.save_pin(served.url, impostor)
        reply = await _to_thread(_hook_post, served.url, brain.machine, monkeypatch)
    assert reply is None


async def test_status_health_check_uses_the_pin(brain, tls, impostor):
    files, pem = tls
    async with serving(brain.app(), tls=files) as served:
        assert not await _to_thread(remote._reachable, served.url, None)
        remote_tls.save_pin(served.url, impostor)
        assert not await _to_thread(remote._reachable, served.url, None)
        remote_tls.save_pin(served.url, pem)
        assert await _to_thread(remote._reachable, served.url, None)


async def _to_thread(fn, *args):
    """Run a blocking client call off the loop the brain is served on."""
    import anyio

    return await anyio.to_thread.run_sync(fn, *args)


@pytest.mark.parametrize("pinned", [True, False])
async def test_promote_reaches_a_brain_only_through_its_pin(brain, tls, pinned):
    from gingugu.promote import _call, _session
    from tests.proxy_fixtures import SERVE_TOKEN

    files, pem = tls
    async with serving(brain.app(), tls=files) as served:
        if not pinned:
            with pytest.raises(BaseException):  # noqa: B017 - the SDK wraps it in a group
                async with _session(f"{served.url}/mcp", SERVE_TOKEN):
                    pass
            return
        remote_tls.save_pin(served.url, pem)
        async with _session(f"{served.url}/mcp", SERVE_TOKEN) as session:
            stats = await _call(session, "memory_stats", {})
    assert stats["ok"]
