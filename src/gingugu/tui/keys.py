"""Any key skips: a non-blocking keypress check that puts the terminal back.

POSIX puts stdin in cbreak mode for the length of the show (no echo, no line
buffering) and restores it on the way out, whatever happens; Windows asks
``msvcrt``. Without a TTY on stdin there is nothing to read and nothing is
touched.
"""

from __future__ import annotations

import sys
from types import TracebackType


class Keys:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._saved: object = None
        self._fd = -1

    def __enter__(self) -> Keys:
        if not self.enabled or sys.platform == "win32":
            return self
        try:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except Exception:
            self.enabled = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._saved is None:
            return
        try:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        except Exception:
            pass
        self._saved = None

    def pressed(self) -> bool:
        """True (and the keys drained) if anything was typed since the last look."""
        if not self.enabled:
            return False
        try:
            if sys.platform == "win32":  # pragma: no cover - Windows only
                import msvcrt

                hit = False
                while msvcrt.kbhit():
                    msvcrt.getwch()
                    hit = True
                return hit
            import os
            import select

            hit = False
            while select.select([self._fd], [], [], 0)[0]:
                if not os.read(self._fd, 64):
                    break
                hit = True
            return hit
        except Exception:
            return False
