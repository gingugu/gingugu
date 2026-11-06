"""SIGTERM and SIGHUP end the show cleanly: terminal back, music off, then die.

A ``kill`` or a closed terminal window never runs ``finally`` blocks, so the
show would leave the terminal in cbreak mode with the cursor hidden and the
player still playing. While a ``Stage`` or ``Music`` is live it registers a
cleanup here; the first one installs handlers for both signals (POSIX, main
thread only), the last one out restores whatever was there before. On a
signal every cleanup runs, newest first, the previous handlers come back, and
the signal is delivered again so the process ends exactly as it would have.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n))
_cleanups: dict[int, Callable[[], None]] = {}
_saved: dict[int, Any] = {}
_next = 0


def _usable() -> bool:
    return sys.platform != "win32" and threading.current_thread() is threading.main_thread()


def push(cleanup: Callable[[], None]) -> int | None:
    """Register ``cleanup``; returns a token for ``pop``, or None where unsupported."""
    global _next
    if not _usable():
        return None
    if not _cleanups:
        try:
            for sig in _SIGNALS:
                _saved[sig] = signal.signal(sig, _handle)
        except (ValueError, OSError):  # pragma: no cover - not the main interpreter
            _restore()
            return None
    _next += 1
    _cleanups[_next] = cleanup
    return _next


def pop(token: int | None) -> None:
    """Drop a cleanup; the last one out puts the previous handlers back."""
    if token is None or _cleanups.pop(token, None) is None:
        return
    if not _cleanups and _usable():
        _restore()


def _restore() -> None:
    for sig, previous in list(_saved.items()):
        try:
            signal.signal(sig, previous if previous is not None else signal.SIG_DFL)
        except (ValueError, OSError):  # pragma: no cover
            pass
    _saved.clear()


def _handle(signum: int, frame: Any) -> None:
    previous = _saved.get(signum, signal.SIG_DFL)
    for token in sorted(_cleanups, reverse=True):
        cleanup = _cleanups.pop(token, None)
        if cleanup is None:
            continue
        try:
            cleanup()
        except Exception:
            pass
    _restore()
    if callable(previous):
        previous(signum, frame)
    elif previous != signal.SIG_IGN:
        os.kill(os.getpid(), signum)  # the default action, now that we're clean
