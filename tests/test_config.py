"""Tests for config: OS-appropriate default DB path."""

from __future__ import annotations

from pathlib import Path

from gingugu.config import _default_db_path


def test_default_db_path_posix_is_xdg_style() -> None:
    # macOS and Linux keep ~/.local/share/gingugu — matches existing installs
    # and the legacy memory-manager migration path.
    for platform in ("darwin", "linux"):
        path = _default_db_path(platform)
        assert path == Path.home() / ".local" / "share" / "gingugu" / "memories.db"


def test_default_db_path_windows_uses_platformdirs(monkeypatch) -> None:
    # platformdirs checks the real OS, so mock it to return a Windows-style
    # path when running on Linux/macOS CI runners.
    fake_appdata = "C:/Users/test/AppData/Local/gingugu"
    monkeypatch.setattr("gingugu.config.platformdirs.user_data_dir", lambda *a, **kw: fake_appdata)
    path = _default_db_path("win32")
    assert path.name == "memories.db"
    assert "gingugu" in path.parts
    # Must NOT be the POSIX dotfile layout on Windows.
    assert ".local" not in path.parts
