"""The CLI's face: a Win95 keygen with a techno loop, for the commands people run.

Only human-facing commands come here (init, harness, remote, credential,
token, promote, embed, help, version, party). Hooks, the stdio server, the
proxy, ``serve``, ``board watch``, ``xray`` and ``dream`` are machine paths
and never import this package, so their bytes cannot change.

Every entry point is a no-op dressing in plain mode (see ``caps``): output a
pipe, a log, CI or ``NO_COLOR`` sees is plain ASCII with the same words.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from ..showable import clean
from . import art, fx, keygen
from . import paint as p
from .audio import Music
from .audio import enabled as music_enabled
from .caps import Caps, detect
from .stage import Stage

__all__ = ["Caps", "detect", "report", "run", "banner", "version_line"]


def _version() -> str:
    from .. import __version__

    return __version__


def _music(caps: Caps) -> Music | None:
    if not music_enabled(caps):
        return None
    music = Music()
    music.start()
    return music


def report(
    results: list[str],
    *,
    dry_run: bool,
    command: str,
    subject: str,
    stream: TextIO | None = None,
    caps: Caps | None = None,
) -> None:
    """Print a bootstrap command's result lines as the full keygen show.

    ``subject`` (a repo directory name) and the result lines (paths, hook
    names) are untrusted: each is cleaned to one escape-free line here, before
    any mode draws it, so the only escapes on screen are the show's own.
    """
    results = [clean(line) for line in results]
    subject = clean(subject)
    stream = sys.stdout if stream is None else stream
    caps = detect(stream) if caps is None else caps
    version = _version()
    if caps.plain:
        stream.write("\n".join(keygen.plain_lines(results, dry_run=dry_run, command=command,
                                                  version=version)) + "\n")  # fmt: skip
        return
    counts = keygen.score(results)
    width = min(caps.width - 1, 64)
    music = _music(caps)
    try:
        with Stage(caps, stream) as stage:
            stage.play(
                lambda t: keygen.window(caps, t, command=command, subject=subject, version=version),
                keygen.SHOW_SECONDS,
            )
            label = "PR3VI3W1NG" if dry_run else "PATCH1NG M3M0RY"
            stage.play(lambda t: [fx.progress(caps, label, t / 0.8, t, width)], 0.8)
            stage.show([""])
            stage.reveal(keygen.body_lines(caps, results))
            stage.play(lambda t: keygen.footer(caps, t, counts, dry_run), 1.0)
    finally:
        if music is not None:
            music.stop()


def _intro(caps: Caps, command: str, version: str) -> Callable[[float], list[str]]:
    width = min(caps.width - 1, 64)
    win = art.Window(caps, f"GiNGUGU v{version} :: {command.upper()}", width)

    def frame(t: float) -> list[str]:
        rows = [win.titlebar(), win.top()]
        rows += [win.row(r) for r in fx.logo(caps, t, win.inner)]
        rows += [win.row(r) for r in fx.rasterbars(caps, t, win.inner, lines=1)]
        return rows + [win.bottom()]

    return frame


def run(command: str, fn: Callable[[], int], *, stream: TextIO | None = None) -> int:
    """Run a human-facing command inside the show: intro, the command, outro.

    The command prints exactly what it always has; the show only adds a
    title before it and the exit code, spelled out, after it.
    """
    stream = sys.stdout if stream is None else stream
    caps = detect(stream)
    if caps.plain:
        return fn()
    music = _music(caps)
    rc: int | None = None
    try:
        with Stage(caps, stream) as stage:
            stage.play(_intro(caps, command, _version()), 1.2)
        stream.flush()
        rc = fn()
        return rc
    finally:
        if music is not None:
            music.stop()
        if rc is not None:
            if rc == 0:
                outro = f"*** {command.upper()} :: L3V3L C0MPL3T3 ***"
                stream.write(p.gradient(outro, fx.NEON, caps.color, bold=True) + "\n")
            else:
                outro = f"*** G4M3 0V3R *** {command} exited {rc}"
                stream.write(p.paint(outro, p.RED, caps.color, bold=True) + "\n")


def banner(stream: TextIO | None = None) -> str:
    """The static logo for ``--help``, or "" in plain mode."""
    caps = detect(sys.stdout if stream is None else stream)
    if caps.plain:
        return ""
    width = min(caps.width - 1, 64)
    rows = fx.logo(caps, 0.7, width)
    tag = p.gradient("M3M0RY F0R M4CH1N3S  //  0N3 SQL1T3 F1L3  //  N0 CL0UD", fx.NEON,
                     caps.color, bold=True)  # fmt: skip
    return "\n".join([*rows, art.center(tag, width), ""]) + "\n"


def version_line(version: str, stream: TextIO | None = None) -> str:
    caps = detect(sys.stdout if stream is None else stream)
    text = f"gingugu {version}"
    return text if caps.plain else p.gradient(text, fx.NEON, caps.color, bold=True)
