"""The stage: draws effect frames in place, then gets out of the way.

A frame is a list of lines. The first is printed; each later one moves the
cursor back up and overwrites it, so the show never scrolls the terminal.
Lines are cut to the terminal width first, because a wrapped line would throw
the cursor arithmetic off and smear the next frame down the screen.

Any key, or Ctrl-C, skips every effect still to come; the command's real
output is printed regardless. The cursor is hidden for the show and always
shown again, and the keyboard is always restored, on any exit.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from types import TracebackType
from typing import TextIO

from . import art
from .caps import Caps
from .keys import Keys

Frame = Callable[[float], list[str]]
HIDE, SHOW = "\033[?25l", "\033[?25h"


class Stage:
    def __init__(self, caps: Caps, stream: TextIO | None = None, *, fps: int = 30) -> None:
        self.caps = caps
        self.stream = sys.stdout if stream is None else stream
        self.fps = fps
        self.skipped = not caps.animate
        self.interrupted = False
        self._keys = Keys(caps.keys and caps.animate)

    def __enter__(self) -> Stage:
        if self.caps.animate:
            self._keys.__enter__()
            self._write(HIDE)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.caps.animate:
            self._write(SHOW)
            self._keys.__exit__(exc_type, exc, tb)

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            self.skipped = True

    def _fit(self, lines: list[str]) -> list[str]:
        return [art.pad(line, self.caps.width - 1) for line in lines]

    def _draw(self, lines: list[str], previous: int) -> int:
        lines = self._fit(lines)
        up = f"\033[{previous}F" if previous else ""
        # \033[J wipes what a taller previous frame left below this one.
        self._write(up + "".join(f"\033[2K{line}\n" for line in lines) + "\033[J")
        return len(lines)

    def show(self, lines: list[str]) -> None:
        """Print ``lines`` once, statically (styled or plain as the caps allow)."""
        self._write("".join(f"{line}\n" for line in lines))

    def play(self, frame: Frame, seconds: float, *, final: bool = True) -> None:
        """Animate ``frame`` for ``seconds``; leave its last frame on screen."""
        if self.skipped:
            if final:
                self.show(frame(seconds))
            return
        if len(frame(0.0)) >= self.caps.height:
            # Taller than the screen: cursor-up cannot reach the top, so it would smear.
            if final:
                self.show(frame(seconds))
            return
        drawn = 0
        start = time.monotonic()
        try:
            while True:
                t = time.monotonic() - start
                if t >= seconds or self._keys.pressed():
                    if t < seconds:
                        self.skipped = True
                    break
                drawn = self._draw(frame(t), drawn)
                time.sleep(max(0.0, 1 / self.fps - (time.monotonic() - start - t)))
        except KeyboardInterrupt:
            self.skipped = self.interrupted = True
        if final:
            self._draw(frame(seconds), drawn)
        elif drawn:
            self._write(f"\033[{drawn}F\033[J")

    def reveal(self, lines: list[str], *, per_line: float = 0.02, budget: float = 0.9) -> None:
        """Type ``lines`` out one at a time, within ``budget`` seconds in total."""
        delay = min(per_line, budget / max(1, len(lines)))
        for line in lines:
            self.show([line])
            if self.skipped:
                continue
            try:
                time.sleep(delay)
                if self._keys.pressed():
                    self.skipped = True
            except KeyboardInterrupt:
                self.skipped = self.interrupted = True
