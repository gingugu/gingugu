"""The soundtrack: deterministic, optional, and never outliving the command."""

from __future__ import annotations

import io
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from gingugu.tui import audio, synth
from gingugu.tui.caps import TRUECOLOR, Caps

LIVE = Caps(color=TRUECOLOR, unicode=True, width=80, animate=True)


def test_wav_is_deterministic_and_valid():
    first = synth.wav_bytes(bars=1)
    assert first == synth.wav_bytes(bars=1)
    with wave.open(io.BytesIO(first)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, synth.RATE)
        assert w.getnframes() == 16 * synth.STEP


def test_full_loop_length_and_no_clipping():
    samples = synth.render()
    assert len(samples) == 8 * 16 * synth.STEP
    assert max(abs(s) for s in samples) < 32767


def test_fingerprint_names_the_score():
    assert synth.fingerprint() == synth.fingerprint()
    assert len(synth.fingerprint()) == 12


@pytest.mark.parametrize(
    "platform, present, expected",
    [
        ("darwin", {"afplay"}, ["/usr/bin/afplay", "T"]),  # fixed path, never PATH
        ("darwin", set(), None),
        ("linux", {"paplay", "aplay"}, ["/bin/paplay", "T"]),
        ("linux", {"aplay"}, ["/bin/aplay", "-q", "T"]),
        ("linux", set(), None),
        ("freebsd14", {"aplay"}, ["/bin/aplay", "-q", "T"]),
        ("win32", {"afplay", "aplay"}, None),  # Windows plays through winsound instead
        ("sunos5", {"aplay"}, None),
    ],
)
def test_player_selection(platform, present, expected):
    def which(name: str) -> str | None:
        return f"/bin/{name}" if name in present else None

    def exists(path: str) -> bool:
        return Path(path).name in present

    got = audio.player_command(Path("T"), platform=platform, which=which, exists=exists)
    assert got == expected


@pytest.mark.parametrize(
    "caps, env, on",
    [
        (LIVE, {}, True),
        (LIVE, {"GINGUGU_QUIET": "1"}, False),
        (LIVE, {"GINGUGU_QUIET": "0"}, True),
        (Caps(color=TRUECOLOR, unicode=True, animate=False), {}, False),  # CI / NO_ANIM
        (Caps(), {}, False),  # plain: pipe, NO_COLOR, TERM=dumb, GINGUGU_PLAIN
    ],
)
def test_music_on_off(caps, env, on):
    assert audio.enabled(caps, env) is on


def test_track_is_cached_outside_the_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    path = audio.track_path()
    assert path.parent == tmp_path and path.suffix == ".wav"
    mtime = path.stat().st_mtime_ns
    assert audio.track_path().stat().st_mtime_ns == mtime  # written once
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX player path")
def test_stop_kills_the_player(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    music = audio.Music(command=lambda path: sleeper)
    music.start()
    music.wait_started()
    proc = music.process
    assert proc is not None and proc.poll() is None
    music.stop()
    assert proc.poll() is not None  # no orphan left playing
    music.stop()  # idempotent


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX player path")
def test_stop_before_start_finishes_never_spawns(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    spawned: list[Path] = []
    music = audio.Music(command=lambda path: spawned.append(path) or ["true"])
    music.stop()
    music.start()
    music.wait_started()
    assert spawned == [] and music.process is None


def test_no_player_is_silence(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    music = audio.Music(command=lambda path: None)
    music.start()
    music.wait_started()
    assert music.process is None
    music.stop()


def test_a_broken_player_is_silence(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    music = audio.Music(command=lambda path: ["/nonexistent/player-xyz", str(path)])
    music.start()
    music.wait_started()
    assert music.process is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_ctrl_c_in_the_command_still_kills_the_player(tmp_path):
    """A real process: SIGINT mid-show, then check the player died with it."""
    marker = tmp_path / "pid"
    script = f"""
import os, sys, time
os.environ["GINGUGU_CACHE_DIR"] = {str(tmp_path)!r}
from gingugu.tui import audio
m = audio.Music(command=lambda p: [sys.executable, "-c", "import time; time.sleep(60)"])
m.start(); m.wait_started()
open({str(marker)!r}, "w").write(str(m.process.pid))
try:
    raise KeyboardInterrupt
finally:
    m.stop()
"""
    run = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)
    assert run.returncode != 0  # the interrupt propagated
    pid = int(marker.read_text())
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
