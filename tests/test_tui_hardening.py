"""The show's edges: fixed player paths, a cache that trusts nothing, signal-safe exits."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gingugu.tui import audio, synth

SRC = Path(__file__).resolve().parents[1] / "src"
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")


def _fake_player(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return exe


@posix
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_player_in_the_cwd_is_never_chosen(tmp_path, monkeypatch, platform):
    for name in ("afplay", "paplay", "aplay"):
        _fake_player(tmp_path, name)
        _fake_player(tmp_path / "rel", name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", "rel"]))
    argv = audio.player_command(Path("/c/t.wav"), platform=platform)
    assert argv is None or argv[0] in ("/usr/bin/afplay", "/usr/bin/paplay", "/usr/bin/aplay")
    assert argv is None or os.path.isabs(argv[0])


@posix
def test_linux_takes_a_player_from_an_absolute_path_entry(tmp_path, monkeypatch):
    exe = _fake_player(tmp_path / "bin", "aplay")
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(tmp_path / "bin")]))
    assert audio.player_command(Path("/c/t.wav"), platform="linux") == [str(exe), "-q", "/c/t.wav"]


def test_macos_uses_the_fixed_afplay():
    argv = audio.player_command(Path("/c/t.wav"), platform="darwin", which=lambda n: "/evil/" + n)
    assert argv is None or argv == ["/usr/bin/afplay", "/c/t.wav"]


def test_the_track_argument_never_looks_like_a_flag():
    argv = audio.player_command(Path("-x.wav"), platform="linux", which=lambda n: "/bin/" + n)
    assert argv is not None and not argv[-1].startswith("-")


def test_a_relative_cache_dir_falls_back_to_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", "relative/cache")
    assert audio.cache_dir().is_absolute()
    assert "relative" not in audio.cache_dir().parts
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path / "a" / ".." / "b"))
    assert audio.cache_dir() == (tmp_path / "b").resolve()


@posix
def test_the_cached_track_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    path = audio.track_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == synth.wav_bytes()


@posix
def test_a_symlinked_track_is_replaced_never_followed(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    target = audio.cache_dir() / f"c0r3-d3pth-{synth.fingerprint()}.wav"
    target.symlink_to(victim)
    path = audio.track_path()
    assert not path.is_symlink() and path.read_bytes()[:4] == b"RIFF"
    assert victim.read_text() == "keep me"


def test_a_corrupt_track_is_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))
    target = audio.cache_dir() / f"c0r3-d3pth-{synth.fingerprint()}.wav"
    target.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    assert audio.track_path().read_bytes() == synth.wav_bytes()


def test_a_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GINGUGU_CACHE_DIR", str(tmp_path))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(audio.os, "replace", boom)
    with pytest.raises(OSError):
        audio.track_path()
    assert list(tmp_path.iterdir()) == []


def test_the_fingerprint_follows_the_synth_code(monkeypatch):
    before = synth.fingerprint()
    monkeypatch.setattr(synth, "_source_digest", lambda: "something-else")
    assert synth.fingerprint() != before


_SIGTERM_CHILD = """
import os, sys, time
from gingugu.tui import audio
from gingugu.tui.caps import TRUECOLOR, Caps
from gingugu.tui.stage import Stage
caps = Caps(color=TRUECOLOR, unicode=True, width=80, height=40, animate=True, keys=True)
m = audio.Music(command=lambda p: [sys.executable, "-c", "import time; time.sleep(60)"])
m.start(); m.wait_started()
with Stage(caps, sys.stdout) as stage:
    open(sys.argv[1], "w").write(str(m.process.pid))
    stage.play(lambda t: ["~~ show ~~"], 30)
"""


@posix
def test_sigterm_mid_show_restores_the_terminal_and_kills_the_player(tmp_path):
    import pty
    import termios

    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    marker = tmp_path / "pid"
    env = {"PYTHONPATH": str(SRC), "GINGUGU_CACHE_DIR": str(tmp_path / "c")}
    env["PATH"] = "/usr/bin:/bin"

    proc = subprocess.Popen(
        [sys.executable, "-c", _SIGTERM_CHILD, str(marker)],
        stdin=slave, stdout=slave, stderr=subprocess.DEVNULL, env=env,
    )  # fmt: skip
    def drain() -> None:  # a pty buffer is small; read while the child writes
        while True:
            try:
                if not os.read(master, 65536):
                    return
            except OSError:
                return

    threading.Thread(target=drain, daemon=True).start()
    try:
        deadline = time.monotonic() + 20
        while not (marker.exists() and marker.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        player = int(marker.read_text())
        modes = termios.ECHO | termios.ICANON  # what cbreak changes; PENDIN is kernel state
        assert termios.tcgetattr(slave)[3] & modes != before[3] & modes  # cbreak mid-show
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == -signal.SIGTERM  # still dies of SIGTERM
        assert termios.tcgetattr(slave)[3] & modes == before[3] & modes  # restored
        time.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(player, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(slave)
        os.close(master)
