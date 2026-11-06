"""`gingugu party`: the whole intro, on demand, until a key is pressed.

The easter egg and the demo reel in one: plasma, copper bars, the sine
scroller, the keygen cracking a serial, and the loop playing under it. Any key
or Ctrl-C ends it. In plain mode it prints the NFO and leaves.
"""

from __future__ import annotations

import sys
from typing import TextIO

from . import art, fx, keygen
from . import paint as p
from .caps import Caps, detect
from .stage import Stage

NFO = """\
  G I N G U G U   -   the brain that never forgets
  ------------------------------------------------
  supplied by ..... crow
  cracked by ...... beepboop
  ripped by ....... tyrone
  type ............ one sqlite file, no cloud, no telemetry
  release ......... v{version}

  greetz: every agent that ever woke up with amnesia.
  run it in a real terminal for the full show.
"""

SCROLL = (
    "*** GINGUGU PARTY MODE *** you found the cr4cktro ... memory for machines, "
    "one sqlite file, no cloud, no api keys, no telemetry ... the graph remembers, "
    "spreading activation wakes the rest ... greetz to crow, beepboop, tyrone, the pi in "
    "the closet and every session that ever said yo ho ... press any key to jack out ... "
)


def _frame(caps: Caps, version: str, width: int):
    def frame(t: float) -> list[str]:
        scene = int(t // 6) % 3
        rows = [p.gradient(art.center("-=[ G1NGUGU P4RTY M0D3 ]=-", width), fx.NEON,
                           caps.color, phase=t * 0.4, bold=True)]  # fmt: skip
        if scene == 0:
            rows += fx.plasma(caps, t, width, lines=4)
            rows += fx.logo(caps, t, width)
            rows += fx.plasma(caps, t + 3, width, lines=2)
        elif scene == 1:
            rows += fx.rasterbars(caps, t, width, lines=3)
            rows += fx.logo(caps, t, width)
            rows += fx.rasterbars(caps, t + 1.3, width, lines=3)
        else:
            rows += keygen.window(caps, (t % 6), command="party", subject="you", version=version)
        rows += fx.scroller(caps, SCROLL, t, width, lines=3)
        rows.append(fx.progress(caps, "B34T", (t % 4) / 4, t, width))
        return rows

    return frame


def main(argv: list[str] | None = None, *, stream: TextIO | None = None) -> int:
    from .. import __version__
    from .audio import Music, enabled

    stream = sys.stdout if stream is None else stream
    caps = detect(stream)
    seconds = 120.0
    if argv and argv[0] in ("-h", "--help"):
        stream.write("usage: gingugu party [SECONDS]  (any key or Ctrl-C to stop)\n")
        return 0
    if argv:
        try:
            seconds = max(1.0, float(argv[0]))
        except ValueError:
            stream.write(f"gingugu party: {argv[0]!r} is not a number of seconds\n")
            return 2
    if not caps.animate:
        stream.write(NFO.format(version=__version__))
        return 0
    width = caps.width - 1
    # Each frame is ~18 rows; clear below the cursor so a short terminal is not smeared.
    music = Music() if enabled(caps) else None
    if music is not None:
        music.start()
    try:
        with Stage(caps, stream) as stage:
            stage.play(_frame(caps, __version__, width), seconds, final=False)
    finally:
        if music is not None:
            music.stop()
    stream.write(p.gradient("*** J4CK3D 0UT *** thanks for partying", fx.NEON, caps.color,
                            bold=True) + "\n")  # fmt: skip
    return 0
