"""Music while the show runs: never blocking, never outliving the command.

The loop from ``synth`` is written once to the user cache dir (never the
repo) and handed to whatever the platform already has: ``afplay`` on macOS,
``paplay`` then ``aplay`` on Linux, ``winsound`` on Windows. No player, no
music, no message. The player runs in its own session with every stream on
/dev/null, and ``stop()`` - also registered with ``atexit`` - terminates it,
so Ctrl-C, an exception or a normal exit all end the music with the command.

On whenever the show animates, unless ``GINGUGU_QUIET`` is set (the mute
switch). The show itself is already off for non-TTY, CI, ``NO_COLOR``,
``TERM=dumb`` and ``GINGUGU_PLAIN``.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from . import synth
from .caps import Caps

_LINUX_PLAYERS = (("paplay", []), ("aplay", ["-q"]))


def enabled(caps: Caps, env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    quiet = env.get("GINGUGU_QUIET", "").strip().lower() not in ("", "0", "false", "no", "off")
    return caps.animate and not quiet


def player_command(
    path: Path, *, platform: str | None = None, which: Callable[[str], str | None] = shutil.which
) -> list[str] | None:
    """The argv that plays ``path`` here, or None: Windows uses ``winsound`` instead."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        exe = which("afplay")
        return [exe, str(path)] if exe else None
    if platform.startswith("linux") or platform.startswith("freebsd"):
        for name, flags in _LINUX_PLAYERS:
            exe = which(name)
            if exe:
                return [exe, *flags, str(path)]
    return None


def cache_dir() -> Path:
    override = os.environ.get("GINGUGU_CACHE_DIR")
    if override:
        return Path(override)
    import platformdirs

    return Path(platformdirs.user_cache_dir("gingugu", appauthor=False))


def track_path() -> Path:
    """The loop on disk, written on first use; a new score gets a new file."""
    path = cache_dir() / f"c0r3-d3pth-{synth.fingerprint()}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(synth.wav_bytes())
        os.replace(tmp, path)  # two commands at once never see half a file
    return path


class Music:
    """One command's soundtrack. ``start`` returns at once; ``stop`` is idempotent."""

    def __init__(self, command: Callable[[Path], list[str] | None] = player_command) -> None:
        self._command = command
        self._proc: subprocess.Popen[bytes] | None = None
        self._winsound = False
        self._stopped = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        atexit.register(self.stop)
        self._thread = threading.Thread(target=self._run, name="gingugu-music", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            path = track_path()
            with self._lock:
                if self._stopped:
                    return
                if sys.platform == "win32":  # pragma: no cover - Windows only
                    import winsound

                    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                    self._winsound = True
                    return
                argv = self._command(path)
                if argv is None:
                    return
                self._proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception:
            return  # music is a garnish: any failure is silence

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            proc, self._proc = self._proc, None
            if self._winsound:  # pragma: no cover - Windows only
                import winsound

                winsound.PlaySound(None, 0)
                self._winsound = False
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception:
                pass

    def wait_started(self, timeout: float = 5.0) -> None:
        """Tests: block until the background start has run."""
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._proc
