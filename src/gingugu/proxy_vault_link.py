"""The local vault behind a real MCP session, so its tools can ask the person.

``credential_delete`` confirms with the user through the client (``confirm``).
That needs what a direct ``call_tool`` lacks: a session that carries the
client's capabilities, and a channel back to the client. So the vault runs as
an MCP server over in-memory streams, handshaken with the client's own
``initialize``, and the proxy relays raw JSON-RPC to it as it does to the brain.

Two things differ from the brain link:

- A vault call must never block the one reader of the client: the person's
  answer to the vault's question arrives on that same reader. ``submit`` only
  hands the call over; the reply comes back on the vault's own pump.
- The vault numbers its requests to the client the way the brain does, so
  each is sent out renamed and restored on the way back. The new name carries
  a per-process nonce: an answer is routed by the id it carries, and a brain
  cannot name its own request after a vault question to collect its answer.

A vault that fails - its handshake, or the server itself - answers the calls it
held with an error and refuses the next ones; it never takes the proxy down.
"""

from __future__ import annotations

import secrets
import sys

import anyio
from anyio.abc import ObjectReceiveStream, ObjectSendStream, TaskGroup
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCRequest, JSONRPCResponse

from .proxy_session import CLIENT_GONE, error, replay_handshake, wrap
from .proxy_vault import LocalVault

NO_VAULT = (
    "credential tools are not available through a remote brain; the vault stays on this machine"
)
_UNAVAILABLE = "the local credential vault is unavailable"


class VaultLink:
    """Relays ``credential_*`` calls to the local vault's own MCP session."""

    def __init__(self, vault: LocalVault, client_write: ObjectSendStream) -> None:
        self.vault = vault
        self.client_write = client_write
        self._recv: ObjectReceiveStream | None = None  # from the vault server
        self._send: ObjectSendStream | None = None  # to the vault server
        self._tg: TaskGroup | None = None
        self._lock = anyio.Lock()
        self._ready = False
        self._dead = False
        self._nonce = secrets.token_hex(8)
        self._asked: dict[str, str | int] = {}  # renamed id -> the vault's own id
        self._pending: set[str | int] = set()  # client calls the vault has not answered

    def _rename(self, vault_id: str | int) -> str:
        return f"vault-{self._nonce}:{vault_id}"

    async def run(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        """Own the streams and the vault server for the proxy's lifetime."""
        async with create_client_server_memory_streams() as (ours, theirs):
            self._recv, self._send = ours
            async with anyio.create_task_group() as tg:
                self._tg = tg
                tg.start_soon(self._serve, *theirs)
                task_status.started()
                await anyio.sleep_forever()

    async def _serve(self, read: ObjectReceiveStream, write: ObjectSendStream) -> None:
        server = self.vault.server
        try:
            await server.run(read, write, server.create_initialization_options())
        except Exception as exc:  # noqa: BLE001 - the proxy outlives its vault
            print(f"gingugu proxy: the local vault stopped ({type(exc).__name__})", file=sys.stderr)
        finally:
            await self._lost()

    async def _lost(self) -> None:
        """The vault is gone: answer every call it held, and refuse the rest."""
        self._dead = True
        held, self._pending = sorted(self._pending, key=str), set()
        for req_id in held:
            try:
                await self.client_write.send(error(req_id, _UNAVAILABLE))
            except Exception:  # noqa: BLE001 - the client may be gone too
                return

    def claims(self, msg) -> bool:
        """Whether ``msg`` is the client's answer to one of the vault's questions."""
        return isinstance(msg, JSONRPCResponse | JSONRPCError) and msg.id in self._asked

    async def answer(self, item: SessionMessage) -> None:
        msg = item.message
        original = self._asked.pop(msg.id)
        try:
            await self._send.send(wrap(msg.model_copy(update={"id": original})))
        except Exception:  # noqa: BLE001 - a dead vault already answered its calls
            await self._lost()

    async def submit(self, item: SessionMessage, init_params: dict | None) -> None:
        """Hand one ``tools/call`` to the vault. Never waits on the vault's answer,
        and never raises into the client reader."""
        msg = item.message
        refused = self.vault.screen(msg.id, msg.params or {})
        if refused is not None:
            await self.client_write.send(refused)
            return
        async with self._lock:
            if not self._ready and not self._dead:
                if init_params is None:
                    await self.client_write.send(error(msg.id, "initialize first"))
                    return
                try:
                    # The client's own initialize: the vault sees its capabilities.
                    await replay_handshake(self._recv, self._send, init_params, 0)
                except Exception as exc:  # noqa: BLE001 - incl. the 30s timeout
                    print(
                        f"gingugu proxy: the local vault did not start ({type(exc).__name__})",
                        file=sys.stderr,
                    )
                    await self._lost()
                else:
                    self._tg.start_soon(self._pump)
                    self._ready = True
        if self._dead or self._send is None:
            await self.client_write.send(error(msg.id, _UNAVAILABLE))
            return
        self._pending.add(msg.id)
        try:
            await self._send.send(item)
        except Exception:  # noqa: BLE001 - the vault died between the check and the send
            await self._lost()

    async def _pump(self) -> None:
        """Vault -> client: replies as they are, the vault's own requests renamed."""
        try:
            async for item in self._recv:
                if isinstance(item, Exception):
                    continue
                msg = item.message
                if isinstance(msg, JSONRPCRequest):
                    renamed = self._rename(msg.id)
                    self._asked[renamed] = msg.id
                    item = wrap(msg.model_copy(update={"id": renamed}))
                elif isinstance(msg, JSONRPCResponse | JSONRPCError):
                    self._pending.discard(msg.id)
                await self.client_write.send(item)
        except CLIENT_GONE:
            return
        except Exception as exc:  # noqa: BLE001 - never end the proxy from here
            print(f"gingugu proxy: vault relay stopped ({type(exc).__name__})", file=sys.stderr)
            await self._lost()
