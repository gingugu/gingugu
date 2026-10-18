"""Each gingugu server tells its client's hooks which grant it runs under.

A client's hooks run in the client's own environment; its MCP server runs in
the ``env`` block of the client's MCP config. A ``MEMORY_GRANT`` set only in the
latter fenced the server and left involuntary recall, tripwires and minion
warm-up unfenced, and nothing said so. So every server publishes the grant it
runs under - ``null`` when unfenced - with its own ancestor chain, and a hook
applies what its client's servers published.

A hook's client is the lowest common ancestor of the hook and the server:
launchers (``uv run``, ``sh -c``, a hook script) sit on one side only, and two
sessions under one host meet further up than each session's own pair. So a
hook takes the publications that meet it at its NEAREST ancestor. Every link
is a ``(pid, start time)`` pair, so a reused pid never matches. No process
names are matched.

A publication is live while its publisher runs (same pid and start time). A
fenced server's publication is removed only on a clean shutdown: one that
crashed, or refused a bad grant, stays behind as a fence, and the hooks of a
client with a fence and no live server refuse rather than open. Servers
started from a minion's agent file mark themselves (``MEMORY_SERVER_ROLE``);
the mark is honoured only beside a non-minion publication, so an inherited
mark cannot hide the main server.

A hook can fire before the server has published (spawned with the client, it
imports for a few hundred ms), so a young client - the nearest ancestor with a
child off the hook's chain, younger than ``STARTUP_WINDOW_S`` - is waited on
for the whole window, so every server it starts gets the chance.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import platformdirs
import psutil

logger = logging.getLogger(__name__)

ROLE_ENV = "MEMORY_SERVER_ROLE"
MINION = "minion"
STARTUP_WINDOW_S = 3.0
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _root() -> Path:
    return Path(platformdirs.user_runtime_dir("gingugu", appauthor=False)) / "clients"


def ancestors() -> list[psutil.Process]:
    """This process's ancestors, nearest first. Raises psutil.Error."""
    return psutil.Process().parents()


def _start(proc) -> float:
    # Rounded: start times are derived (boot time + ticks on Linux), and a
    # comparison must not hinge on float noise between two processes' reads.
    return round(proc.create_time(), 2)


def _links(procs: list) -> list[tuple[int, float]]:
    return [(p.pid, _start(p)) for p in procs]


def _alive(pid: int, created: float) -> bool:
    try:
        return _start(psutil.Process(pid)) == created
    except psutil.Error:
        return False


def find_client(procs: list) -> object | None:
    """The nearest of ``procs`` with a child off the caller's own chain."""
    chain = {os.getpid()}
    for proc in procs:
        if any(child.pid not in chain for child in proc.children()):
            return proc
        chain.add(proc.pid)
    return None


def _owned(path: Path) -> bool:
    if os.name != "posix":
        return True  # %LOCALAPPDATA% is per-user; mode bits mean nothing there
    return path.lstat().st_uid == os.getuid() and not path.is_symlink()


def _fenced(entry: dict) -> bool:
    return entry.get("grant") is not None and entry.get("role") != MINION


def _read(path: Path) -> dict:
    if not _owned(path):
        raise ValueError(f"grant publication {path} is not this user's")
    entry = json.loads(path.read_text())
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("grant"), str | None)
        or not isinstance(entry.get("chain"), list)
    ):
        raise ValueError(f"malformed grant publication {path}")
    return entry


def _sweep(root: Path) -> None:
    """Drop dead publications: unfenced ones at once, a fence once its tree is gone."""
    for path in root.glob("*.json"):
        try:
            entry = _read(path)
            if _alive(int(path.stem), entry.get("created")):
                continue
            # Kept while anything above the OS root in its chain lives: that may
            # still be the client this fence belongs to.
            links = [tuple(link) for link in entry["chain"] if link[0] > 1]
            if _fenced(entry) and any(_alive(pid, created) for pid, created in links):
                continue
            path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            continue


def publish(spec: str | None) -> Path | None:
    """Record this server's grant for its client's hooks. Never raises.

    The caller removes it with ``release`` on a clean shutdown only.
    """
    try:
        me = psutil.Process()
        root = _root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not _owned(root):
            raise OSError(f"{root} is not this user's")
        _sweep(root)
        path = root / f"{me.pid}.json"
        tmp = root / f"{me.pid}.json.tmp"
        tmp.unlink(missing_ok=True)
        body = {
            "grant": spec,
            "role": os.environ.get(ROLE_ENV) or None,
            "created": _start(me),
            "chain": _links(ancestors()),
        }
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(body))
        os.replace(tmp, path)
    except Exception as exc:  # a server must start even if its hooks cannot be told
        logger.warning("could not publish this server's grant for its hooks: %s", exc)
        return None
    return path


def release(path: Path | None) -> None:
    """Withdraw a publication at a clean shutdown."""
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def release_on_signal(path: Path | None) -> None:
    """Withdraw on SIGTERM / SIGINT, then die of the signal exactly as before.

    A client ends its servers with a signal, not by closing stdin and waiting
    (measured: Claude Code 2.1.289 headless left every publication behind). That
    stop is a clean one from the client's side, so it withdraws; a crash still
    leaves a fence. The default action is restored and the signal re-raised,
    so the process exits the way it always did.
    """
    import signal

    def _handler(signum, _frame) -> None:
        release(path)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    if path is None:
        return
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):  # not the main thread, or unsupported
                pass


def _nearest(procs: list, bound: int | None) -> list[tuple[dict, bool]] | None:
    """Publications meeting this hook at its nearest ancestor, with liveness.

    A meeting above ``procs[bound]`` (this hook's client) is another session's
    server under a shared shell or host, and is not this hook's to apply.
    """
    root = _root()
    if not root.is_dir():
        return None
    if not _owned(root):
        raise ValueError(f"{root} is not this user's")
    mine = _links(procs if bound is None else procs[: bound + 1])
    groups: dict[int, list[tuple[dict, bool]]] = {}
    for path in root.glob("*.json"):
        try:
            entry = _read(path)
        except FileNotFoundError:
            continue  # it shut down while we listed
        except (OSError, ValueError) as exc:
            raise ValueError(f"unreadable grant publication {path}") from exc
        theirs = {tuple(link) for link in entry["chain"] if isinstance(link, list)}
        meet = next((i for i, link in enumerate(mine) if link in theirs), None)
        if meet is not None:
            live = _alive(int(path.stem), entry.get("created"))
            groups.setdefault(meet, []).append((entry, live))
    return groups[min(groups)] if groups else None


def client_publications() -> list[str | None] | None:
    """The grants of the servers of the client this hook runs under.

    None when none was published. Raises ValueError when the process tree
    cannot be read, a publication cannot be read, or the client is fenced and
    none of its servers is running.
    """
    try:
        procs = ancestors()
        client = find_client(procs)
        bound = None
        if client is not None:
            bound = procs.index(client)
            wait = client.create_time() + STARTUP_WINDOW_S - time.time()
            if wait > 0:
                time.sleep(wait)
        entries = _nearest(procs, bound)
    except psutil.Error as exc:
        raise ValueError(f"cannot read this hook's process tree: {exc}") from exc
    if entries is None:
        return None
    live = [entry for entry, alive in entries if alive]
    main = [e for e in live if e.get("role") != MINION] or live
    if main:
        return [e["grant"] for e in main]
    if any(_fenced(entry) for entry, _ in entries):
        raise ValueError("this client is fenced but none of its gingugu servers is running")
    return None
