"""A minion whose brain cannot load says so (found 2026-10-17).

Claude Code ignores the inline ``mcpServers`` of a PROJECT agent file until
the folder is trusted (``projects[<dir>].hasTrustDialogAccepted`` in
``~/.claude.json``, which is what the CLI names when it refuses). The minion
then starts with no ``brain`` tool and nothing tells anyone why. The warm-up
hook now names it, and ``gingugu harness`` warns when it installs into an
untrusted repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gingugu import claude_trust, subagent_hook


def _config(home: Path, projects: dict) -> Path:
    path = home / ".claude.json"
    path.write_text(json.dumps({"projects": projects}))
    return path


def test_a_trusted_folder(tmp_path) -> None:
    cfg = _config(tmp_path, {"/w/repo": {"hasTrustDialogAccepted": True}})
    assert claude_trust.trusted(Path("/w/repo"), cfg) is True


@pytest.mark.parametrize(
    "projects", [{}, {"/w/repo": {}}, {"/w/repo": {"hasTrustDialogAccepted": False}}]
)
def test_an_untrusted_folder(tmp_path, projects) -> None:
    cfg = _config(tmp_path, projects)
    assert claude_trust.trusted(Path("/w/repo"), cfg) is False


def test_a_trusted_parent_counts(tmp_path) -> None:
    cfg = _config(tmp_path, {"/w": {"hasTrustDialogAccepted": True}})
    assert claude_trust.trusted(Path("/w/repo"), cfg) is True


@pytest.mark.parametrize("body", [None, "{not json", "[]"])
def test_an_unreadable_config_is_unknown(tmp_path, body) -> None:
    cfg = tmp_path / ".claude.json"
    if body is not None:
        cfg.write_text(body)
    assert claude_trust.trusted(Path("/w/repo"), cfg) is None


# --- the warm-up names it -----------------------------------------------------

_AGENT = """---
name: scout
tools: Read, mcp__brain
mcpServers:
  - brain:
      type: stdio
      command: gingugu
      env:
        MEMORY_GRANT: "crow=read"
---
body
"""


def _project(tmp_path, monkeypatch, trusted: bool) -> Path:
    repo = tmp_path / "repo"
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "scout.md").write_text(_AGENT)
    home = tmp_path / "home"
    home.mkdir()
    projects = {str(repo): {"hasTrustDialogAccepted": True}} if trusted else {}
    cfg = _config(home, projects)
    monkeypatch.setattr(claude_trust, "config_path", lambda: cfg)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "absent.db"))
    return repo


def _run(capsys) -> str:
    subagent_hook.run({"agent_type": "scout", "session_id": "s1"})
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_warmup_says_the_brain_will_not_load_in_an_untrusted_repo(
    tmp_path, monkeypatch, capsys
) -> None:
    _project(tmp_path, monkeypatch, trusted=False)
    context = _run(capsys)
    assert "brain" in context and "not trusted" in context


def test_warmup_is_silent_about_trust_in_a_trusted_repo(tmp_path, monkeypatch, capsys) -> None:
    _project(tmp_path, monkeypatch, trusted=True)
    assert "not trusted" not in _run(capsys)


def test_a_user_level_agent_file_needs_no_trust(tmp_path, monkeypatch, capsys) -> None:
    repo = _project(tmp_path, monkeypatch, trusted=False)
    home = Path.home()
    (home / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "scout.md").rename(home / ".claude" / "agents" / "scout.md")
    assert "not trusted" not in _run(capsys)


# --- gingugu harness ------------------------------------------------------------


def test_harness_warns_when_the_repo_is_untrusted(tmp_path, monkeypatch, capsys) -> None:
    from gingugu.bootstrap.harness import main

    repo = tmp_path / "my-repo"
    repo.mkdir()
    cfg = _config(tmp_path, {})
    monkeypatch.setattr(claude_trust, "config_path", lambda: cfg)
    assert main(["--path", str(repo)]) == 0
    assert "not trusted" in capsys.readouterr().out


def test_harness_says_nothing_when_trusted(tmp_path, monkeypatch, capsys) -> None:
    from gingugu.bootstrap.harness import main

    repo = tmp_path / "my-repo"
    repo.mkdir()
    cfg = _config(tmp_path, {str(repo): {"hasTrustDialogAccepted": True}})
    monkeypatch.setattr(claude_trust, "config_path", lambda: cfg)
    assert main(["--path", str(repo)]) == 0
    assert "not trusted" not in capsys.readouterr().out


def test_harness_minions_mark_their_server(tmp_path) -> None:
    from gingugu.bootstrap.harness import main
    from gingugu.client_grants import MINION, ROLE_ENV

    repo = tmp_path / "my-repo"
    repo.mkdir()
    assert main(["--path", str(repo)]) == 0
    agents = sorted((repo / ".claude" / "agents").glob("*.md"))
    assert agents
    for agent in agents:
        assert f'{ROLE_ENV}: "{MINION}"' in agent.read_text(), agent.name
