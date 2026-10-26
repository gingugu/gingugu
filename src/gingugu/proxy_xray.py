"""``code_xray`` through a remote brain: read the checkout here, ask the brain there.

The brain cannot read the caller's repository - under `gingugu serve` a path is
the host's disk, and the tool there refuses one. So the proxy claims every
``code_xray`` call that would need the disk (``blast``, ``overview``,
``refresh`` without a ``fingerprint`` or ``payload``) and does that part on
this machine, through ``xray.local``, the same code the local server runs:

1. fingerprint the checkout (cheap: no parsing) and ask the brain by fingerprint;
2. if the brain has no snapshot of that content, extract it, ``ingest`` the
   payload, and ask again.

The brain only ever receives a fingerprint, repo-relative file names, and a
payload of names and edges. Never a path.

**Never blocks the client reader.** Extraction can take seconds on a large
repository, and the reader is the one task every other request rides on, so a
claimed call is handed to a task and answered on its own. Each call opens its
own short MCP session to the brain with the proxy's session token, so nothing
about the relay's link, replay or reconnect logic is involved.
"""

from __future__ import annotations

import json

import anyio
from anyio.abc import ObjectSendStream, TaskGroup
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, JSONRPCNotification, JSONRPCRequest, TextContent

from .proxy_auth import SessionTokens, brain_client
from .proxy_session import CLIENT_GONE
from .proxy_vault import _refused, _reply
from .xray import extract, local

NAME = "code_xray"
_LOCAL = ("blast", "overview", "refresh")


def claims(msg) -> bool:
    """True for a ``code_xray`` call that needs this machine's disk."""
    if not isinstance(msg, JSONRPCRequest) or msg.method != "tools/call":
        return False
    params = msg.params or {}
    args = params.get("arguments") or {}
    return (
        params.get("name") == NAME
        and args.get("action") in _LOCAL
        and not args.get("fingerprint")
        and args.get("payload") is None
    )


def _data(result: CallToolResult) -> dict:
    try:
        return json.loads(result.content[0].text)
    except (IndexError, AttributeError, ValueError):
        return {}


def _checked(result: CallToolResult) -> CallToolResult:
    """The brain answers a fingerprint ``exact``; this proxy just compared that
    fingerprint with the checkout, so the answer is ``current``."""
    data = _data(result)
    if data.get("freshness") != "exact":
        return result
    data["freshness"] = "current"
    content = [TextContent(type="text", text=json.dumps(data, indent=2))]
    structured = result.structured_content
    if isinstance(structured, dict) and structured.get("freshness") == "exact":
        structured = {**structured, "freshness": "current"}
    return result.model_copy(update={"content": content, "structured_content": structured})


def _cancelled_id(msg):
    if isinstance(msg, JSONRPCNotification) and msg.method == "notifications/cancelled":
        return (msg.params or {}).get("requestId")
    return None


async def _thread(fn, *args):
    # Abandoned on cancel: git and ast cannot be interrupted, and a cancelled
    # call's result is discarded, never ingested or answered.
    return await anyio.to_thread.run_sync(fn, *args, abandon_on_cancel=True)


class XrayRelay:
    """Answers claimed ``code_xray`` calls, each in its own cancellable task."""

    def __init__(
        self, url: str, tokens: SessionTokens, client_write: ObjectSendStream, tg: TaskGroup
    ) -> None:
        self._url = url
        self._tokens = tokens
        self._client_write = client_write
        self._tg = tg
        self._scopes: dict = {}  # request id -> the cancel scope of its task

    @property
    def running(self) -> bool:
        return bool(self._scopes)

    def take(self, item: SessionMessage) -> bool:
        """Claim ``item`` if it needs this machine's disk, or cancels a call that does."""
        req_id = _cancelled_id(item.message)
        if req_id is not None and req_id in self._scopes:
            self._scopes[req_id].cancel()  # MCP: a cancelled request gets no response
            return True
        if not claims(item.message):
            return False
        self._tg.start_soon(self._run, item)
        return True

    async def _run(self, item: SessionMessage) -> None:
        msg = item.message
        args = dict((msg.params or {}).get("arguments") or {})
        with anyio.CancelScope() as scope:
            self._scopes[msg.id] = scope
            try:
                result = await self._answer(args)
            except (extract.XrayError, ValueError) as exc:
                result = _refused(str(exc))
            except Exception as exc:  # noqa: BLE001 - one call fails, never the proxy
                result = _refused(f"code_xray failed on this machine: {type(exc).__name__}: {exc}")
            finally:
                self._scopes.pop(msg.id, None)
            try:
                await self._client_write.send(_reply(msg.id, result))
            except CLIENT_GONE:
                pass

    async def _call(self, args: dict) -> CallToolResult:
        http = brain_client(self._url, self._tokens)
        transport = streamable_http_client(f"{self._url}/mcp", http_client=http)
        async with http, Client(transport) as client:
            return await client.call_tool(NAME, args)

    async def _ingest(self, repo, namespace, *, replace: bool) -> tuple[CallToolResult, str]:
        x = await _thread(extract.extract, repo)
        sent = {"action": "ingest", "payload": x.payload(), "replace": replace}
        if namespace is not None:
            sent["namespace"] = namespace
        return await self._call(sent), x.fingerprint

    async def _answer(self, args: dict) -> CallToolResult:
        repo = await _thread(local.root, args.pop("path", None))
        namespace = args.get("namespace")
        if args["action"] == "refresh":
            return (await self._ingest(repo, namespace, replace=True))[0]
        if "files" in args:
            args["files"] = local.seeds(local.file_list(args["files"]), repo)
        args["fingerprint"] = await _thread(extract.fingerprint, repo)
        result = await self._call(args)
        if not _data(result).get("missing_snapshot"):
            return _checked(result)
        ingested, args["fingerprint"] = await self._ingest(repo, namespace, replace=False)
        if _data(ingested).get("ok") is False:
            return ingested
        return _checked(await self._call(args))
