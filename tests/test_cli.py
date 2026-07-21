"""Tests for the `gingugu` console entry point dispatch (server.main)."""

from __future__ import annotations

import pytest

from gingugu import __version__, server


def _run(monkeypatch, args):
    """Invoke server.main() with a synthetic argv (argv[0] is the prog name)."""
    monkeypatch.setattr("sys.argv", ["gingugu", *args])
    return server.main()


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_help_prints_usage_and_exits_zero(monkeypatch, capsys, flag):
    _run(monkeypatch, [flag])
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "gingugu serve" in out and "gingugu init" in out


@pytest.mark.parametrize("flag", ["-V", "--version", "version"])
def test_version_prints_version(monkeypatch, capsys, flag):
    _run(monkeypatch, [flag])
    out = capsys.readouterr().out
    assert out.strip() == f"gingugu {__version__}"


def test_unknown_command_errors_to_stderr(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["bogus"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unknown command 'bogus'" in err
    assert "Usage:" in err  # usage is echoed to help the user recover


def test_bare_invocation_runs_stdio_server(monkeypatch):
    calls = {"run": 0}

    class _FakeServer:
        def run(self):
            calls["run"] += 1

    monkeypatch.setattr(server, "build_server", lambda: _FakeServer())
    _run(monkeypatch, [])
    assert calls["run"] == 1


@pytest.mark.parametrize(
    "sub, target",
    [
        ("serve", "gingugu.serve.serve"),
        ("promote", "gingugu.promote.main"),
    ],
)
def test_subcommands_dispatch(monkeypatch, sub, target):
    seen = {"called": False}
    monkeypatch.setattr(target, lambda *a, **k: seen.__setitem__("called", True))
    _run(monkeypatch, [sub])
    assert seen["called"] is True


def test_init_dispatch_propagates_exit_code(monkeypatch):
    monkeypatch.setattr("gingugu.bootstrap.main", lambda argv: 7)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["init"])
    assert exc.value.code == 7
