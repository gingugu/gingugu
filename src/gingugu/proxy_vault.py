"""The credential vault, served by the proxy itself in remote mode.

The vault is this machine's: secret values live in its OS keychain and the
metadata in its local DB, so a remote brain cannot serve it. With credentials
enabled the proxy keeps ``credential_*`` here, running the same handlers a
local stdio server registers (``handlers.credentials``), and relays everything
else. A credential call never reaches the brain, and it still answers while the
brain is down.

The local DB is opened only for its vault tables. Its memories are not read in
remote mode.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, JSONRPCResponse, TextContent

from .credentials import CredentialVault
from .database import Database
from .handlers import ServerContext
from .handlers import credentials as credential_handlers
from .proxy_session import ProxyLost, wrap


class LocalVault:
    """``credential_*`` tools over this machine's DB and keychain."""

    def __init__(self, db_path: Path) -> None:
        self._db = Database(db_path)
        # Only `conn` and `transport` are read by the credential handlers.
        # "stdio": `into` writes to this machine's disk, as on a local server.
        ctx = ServerContext(
            config=None, store=None, namespaces=None, conn=self._db.connect(), transport="stdio"
        )
        self._mcp = FastMCP("gingugu-vault")
        credential_handlers.register(self._mcp, ctx)
        self.tools: list[dict] = []

    async def load(self) -> LocalVault:
        tools = await self._mcp.list_tools()
        self.tools = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]
        return self

    async def call(self, req_id: str | int, params: dict):
        """Answer one ``tools/call`` as a JSON-RPC response for the client."""
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        if name == "credential_get" and args.get("reveal") not in (None, False):
            # Every other tool here is relayed to the brain, so a steered model
            # could carry an inline secret off this machine. `into` cannot.
            return _reply(req_id, _refused(_NO_REVEAL))
        try:
            out = await self._mcp.call_tool(name, args)
            if isinstance(out, tuple):  # (content, structured) for a tool with an output schema
                content, structured = out
                result = CallToolResult(content=list(content), structuredContent=structured)
            else:
                result = CallToolResult(content=list(out))
        except ToolError as exc:
            result = _failed(str(exc))
        except Exception as exc:  # noqa: BLE001 - the proxy must never crash on a tool
            result = _failed(f"{name} failed: {type(exc).__name__}")
        return _reply(req_id, result)

    def health(self) -> dict:
        """The vault summary ``memory_stats`` reports; metadata only, no keychain."""
        return CredentialVault(self._db.connect()).health()

    def close(self) -> None:
        self._db.close()


def patch_stats(result: dict, health: dict) -> dict:
    """Swap the brain's credential summary in a ``memory_stats`` result for ``health``.

    The brain counts its own vault, which in remote mode is not this machine's.
    Anything that is not a recognisable stats reply passes through untouched.
    """
    for block in result.get("content") or []:
        if block.get("type") != "text":
            continue
        try:
            data = json.loads(block.get("text", ""))
        except (TypeError, ValueError):
            continue
        if _swap(data, health):
            block["text"] = json.dumps(data, indent=2)
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and not _swap(structured, health):
        _swap(structured.get("result"), health)  # FastMCP's wrapped form
    return result


def _swap(data, health: dict) -> bool:
    if not isinstance(data, dict):
        return False
    for key in ("global", "stats"):  # comma-list shape, then single/unscoped shape
        holder = data.get(key)
        if isinstance(holder, dict) and "credentials" in holder:
            holder["credentials"] = dict(health)
            return True
    return False


_NO_REVEAL = (
    "reveal is not available with a remote brain; pass `into` (an absolute path) "
    "with one secret field to use the value"
)


def _reply(req_id: str | int, result: CallToolResult):
    body = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    return wrap(JSONRPCResponse(jsonrpc="2.0", id=req_id, result=body))


def _refused(text: str) -> CallToolResult:
    """A handler-shaped ``{"ok": false}`` result, as `handlers.helpers._err` gives."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"ok": False, "error": text}, indent=2))]
    )


def _failed(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


async def open_vault(db_path: Path | None) -> LocalVault | None:
    """The vault over ``db_path``, or None when credentials are off.

    A vault that cannot open ends the proxy with a message, never a traceback,
    and never by quietly dropping the vault.
    """
    if db_path is None:
        return None
    try:
        return await LocalVault(db_path).load()
    except Exception as exc:  # noqa: BLE001 - any open/migrate failure
        raise ProxyLost(
            f"the local credential vault ({db_path}) could not be opened "
            f"({type(exc).__name__}); set MEMORY_CREDENTIALS_ENABLED=false to run without it"
        ) from None
