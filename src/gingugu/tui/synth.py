"""The soundtrack, synthesized from nothing: "C0R3 D3PTH", an original 8-bar loop.

140 BPM in A minor, Am - F - C - G - Am - F - G - E. A 25% pulse lead runs
16th-note arpeggios over the chord, a square bass jumps octaves on the 8ths,
and the drums are maths: a sine kick swept 150 -> 45 Hz, a noise snare on 2
and 4, a differenced-noise hat on the off-beats. The noise comes from a fixed
LCG, so the same code always writes the same bytes, and the WAV can be cached
by a hash of the score.

Pure stdlib (``array`` + ``wave``); about half a second of CPU, once.
"""

from __future__ import annotations

import array
import hashlib
import io
import math
import wave

RATE = 16000
BPM = 140
STEP = round(RATE * 60 / BPM / 4)  # one 16th note, in samples
# (root midi, chord intervals) per bar
SCORE = [
    (57, (0, 3, 7)),  # Am
    (53, (0, 4, 7)),  # F
    (60, (0, 4, 7)),  # C
    (55, (0, 4, 7)),  # G
    (57, (0, 3, 7)),  # Am
    (53, (0, 4, 7)),  # F
    (55, (0, 4, 7)),  # G
    (52, (0, 4, 7)),  # E
]
ARP = (0, 1, 2, 3, 4, 3, 2, 1, 0, 1, 2, 3, 4, 5, 4, 3)
VERSION = f"c0r3-d3pth-1:{RATE}:{BPM}:{SCORE}:{ARP}"


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


class _Noise:
    """A 31-bit LCG: deterministic white noise in -1..1."""

    def __init__(self, seed: int = 0x6106060) -> None:
        self.state = seed

    def __call__(self) -> float:
        self.state = (self.state * 1103515245 + 12345) & 0x7FFFFFFF
        return self.state / 0x3FFFFFFF - 1.0


def _pulse(phase: float, duty: float) -> float:
    return 1.0 if phase % 1.0 < duty else -1.0


def render(bars: int | None = None) -> array.array:
    """The loop as signed 16-bit mono samples at ``RATE``."""
    score = SCORE if bars is None else SCORE[:bars]
    noise = _Noise()
    out = array.array("h")
    hat_prev = 0.0
    lead_ph = bass_ph = kick_ph = 0.0
    for bar, (root, chord) in enumerate(score):
        tones = [root + 12 + i for i in chord] + [root + 24 + i for i in chord]
        for step in range(16):
            lead_f = _freq(tones[ARP[step]])
            bass_f = _freq(root - 12 + (12 if step % 4 == 2 else 0))
            kick = step % 4 == 0
            snare = step in (4, 12)
            hat = step % 4 == 2
            fill = bar == len(score) - 1 and step >= 12  # snare roll into the loop
            for n in range(STEP):
                t = n / RATE
                lead_ph += lead_f / RATE
                bass_ph += bass_f / RATE
                s = 0.22 * _pulse(lead_ph, 0.25) * math.exp(-t * 9)
                s += 0.20 * _pulse(bass_ph, 0.5) * (1.0 if n < STEP * 0.8 else 0.0)
                if kick and t < 0.12:
                    kick_ph += (45 + 105 * math.exp(-t * 30)) / RATE
                    s += 0.55 * math.sin(2 * math.pi * kick_ph) * math.exp(-t * 18)
                if snare or fill:
                    s += 0.30 * noise() * math.exp(-t * (40 if fill else 22))
                if hat:
                    white = noise()
                    s += 0.12 * (white - hat_prev) * math.exp(-t * 90)
                    hat_prev = white
                out.append(max(-32767, min(32767, int(s * 26000))))
    return out


def wav_bytes(bars: int | None = None) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        samples = render(bars)
        if samples.itemsize != 2:  # pragma: no cover - every CPython has 2-byte 'h'
            raise RuntimeError("array 'h' is not 16-bit here")
        import sys

        if sys.byteorder == "big":  # pragma: no cover - WAV is little-endian
            samples.byteswap()
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def _source_digest() -> str:
    """A hash of this module's code, so a changed renderer never plays an old file."""
    try:
        with open(__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:  # pragma: no cover - a frozen build without sources
        return ""


def fingerprint() -> str:
    """Names the cached WAV: a new score or a new renderer never plays an old file."""
    return hashlib.sha256(f"{VERSION}:{_source_digest()}".encode()).hexdigest()[:12]
