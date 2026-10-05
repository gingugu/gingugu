"""`gingugu harness` - the Claude Code harness, installed on top of `gingugu init`.

Plain ``init`` stays gingugu-only. ``harness`` runs ``init`` and then adds the
rest of the kit: a safety guard and event logging, three fenced minions, the
``creating-pr`` skill, the ``.ai/`` knowledge base, and a managed block in the
repo's ``CLAUDE.md``. Nothing it installs calls out to the network.
"""

from __future__ import annotations

import json
import sys

import pytest

from gingugu.bootstrap._files import TEMPLATE_SIGNATURE
from gingugu.bootstrap.harness import HARNESS_BEGIN, HARNESS_END, main
from gingugu.bootstrap.global_rules import BEGIN_MARKER

HOOKS = ("pre_tool_use.py", "log_event.py", "pre_compact.py")
AGENTS = {"repo-scout.md": "haiku", "security-reviewer.md": "sonnet", "ai-docs-auditor.md": "sonnet"}
SKILL_FILES = ("SKILL.md", "ai-assessment-checklist.md", "stacking-prs.md")
AI_FILES = (
    "memory.md",
    "plans/status.md",
    "specs/01-architecture.md",
    "specs/product-spec.md",
    "specs/dataflow.md",
    "standards/01-code-and-testing.md",
)
LOGGED_EVENTS = (
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "SessionEnd",
    "PostCompact",
    "PermissionRequest",
    "StopFailure",
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "ConfigChange",
    "CwdChanged",
    "FileChanged",
    "InstructionsLoaded",
    "Elicitation",
    "ElicitationResult",
)
# What the user cut: no voice, no LLM calls, nothing that needs an API key.
FORBIDDEN_IN_HOOKS = ("api_key", "anthropic", "openai", "elevenlabs", "pyttsx3", "dotenv", "urllib", "requests", "http")


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "my-repo"
    path.mkdir()
    return path


def _run(repo, *extra):
    return main(["--path", str(repo), *extra])


def _commands(settings, event):
    return [h["command"] for group in settings["hooks"].get(event, []) for h in group["hooks"]]


def _snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- what gets installed --------------------------------------------------------


def test_installs_the_harness_hooks(repo):
    assert _run(repo) == 0
    for name in HOOKS:
        assert TEMPLATE_SIGNATURE in (repo / ".claude" / "hooks" / name).read_text()


def test_runs_init_first(repo):
    _run(repo)
    assert (repo / ".claude" / "hooks" / "session_start.py").exists()
    assert (repo / ".claude" / "hooks" / "user_prompt_recall.py").exists()


def test_hooks_carry_no_network_or_api_key_code(repo):
    _run(repo)
    for name in HOOKS:
        text = (repo / ".claude" / "hooks" / name).read_text().lower()
        for word in FORBIDDEN_IN_HOOKS:
            assert word not in text, f"{name} mentions {word!r}"


def test_agents_are_fenced_to_this_repos_namespace(repo):
    _run(repo)
    for name, model in AGENTS.items():
        text = (repo / ".claude" / "agents" / name).read_text()
        assert TEMPLATE_SIGNATURE in text
        assert "MEMORY_NAMESPACE: my-repo" in text
        assert 'MEMORY_GRANT: "crow=read,my-repo=read,minions=write"' in text
        assert 'MEMORY_CREDENTIALS_ENABLED: "false"' in text
        assert "disallowedTools: Agent, mcp__gingugu" in text
        assert f"model: {model}" in text
        assert "{{" not in text


def test_installs_the_creating_pr_skill(repo):
    _run(repo)
    skill = repo / ".claude" / "skills" / "creating-pr"
    for name in SKILL_FILES:
        assert TEMPLATE_SIGNATURE in (skill / name).read_text()
    assert "name: creating-pr" in (skill / "SKILL.md").read_text()


def test_scaffolds_the_ai_knowledge_base(repo):
    _run(repo)
    for rel in AI_FILES:
        assert (repo / ".ai" / rel).read_text().strip()


def test_claude_md_gets_the_protocol_and_the_harness_block(repo):
    _run(repo)
    text = (repo / "CLAUDE.md").read_text()
    assert BEGIN_MARKER in text  # init's memory protocol, same run
    assert HARNESS_BEGIN in text and HARNESS_END in text
    assert "/creating-pr" in text and ".ai/plans/status.md" in text


def test_settings_wire_every_harness_event(repo):
    _run(repo)
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    for event in LOGGED_EVENTS:
        assert any("log_event.py" in c for c in _commands(settings, event)), event
    pre_tool = _commands(settings, "PreToolUse")
    assert any("pre_tool_use.py" in c for c in pre_tool)
    assert any("pre_tool_tripwire.py" in c for c in pre_tool)  # init's, kept
    assert any("pre_compact.py --backup" in c for c in _commands(settings, "PreCompact"))


def test_settings_deny_opus_minions(repo):
    _run(repo)
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    assert "Agent(model:opus)" in settings["permissions"]["deny"]
    assert "mcp__brain" in settings["permissions"]["allow"]


def test_existing_settings_are_kept(repo):
    claude = repo / ".claude"
    claude.mkdir()
    mine = {"matcher": "", "hooks": [{"type": "command", "command": "my-own-logger"}]}
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(curl:*)"]},
                "hooks": {"PostToolUse": [mine]},
                "model": "sonnet",
            }
        )
    )
    _run(repo)
    settings = json.loads((claude / "settings.json").read_text())
    assert "Bash(ls:*)" in settings["permissions"]["allow"]
    assert "Bash(curl:*)" in settings["permissions"]["deny"]
    assert "my-own-logger" in _commands(settings, "PostToolUse")
    assert settings["model"] == "sonnet"


# --- re-runs, --force, user content ----------------------------------------------


def test_rerun_changes_nothing(repo):
    _run(repo)
    before = _snapshot(repo)
    _run(repo)
    assert _snapshot(repo) == before


def test_ai_files_are_never_overwritten_even_with_force(repo):
    _run(repo)
    status = repo / ".ai" / "plans" / "status.md"
    status.write_text("mine\n")
    _run(repo, "--force")
    assert status.read_text() == "mine\n"
    assert not (status.parent / "status.md.bak").exists()


def test_force_backs_up_an_edited_managed_hook(repo):
    _run(repo)
    guard = repo / ".claude" / "hooks" / "pre_tool_use.py"
    shipped = guard.read_text()
    guard.write_text(shipped + "\n# my tweak\n")
    _run(repo, "--force")
    assert guard.read_text() == shipped
    assert "# my tweak" in (guard.parent / "pre_tool_use.py.bak").read_text()


def test_claude_md_keeps_user_content_and_refreshes_only_its_block(repo):
    claude = repo / "CLAUDE.md"
    claude.write_text("# My rules\n\nkeep me\n")
    _run(repo)
    text = claude.read_text()
    assert text.startswith("# My rules\n\nkeep me\n")

    claude.write_text(text.replace("/creating-pr", "/nope") + "\ntrailing mine\n")
    _run(repo)
    final = claude.read_text()
    assert "/creating-pr" in final and "/nope" not in final
    assert "keep me" in final and "trailing mine" in final
    assert final.count(HARNESS_BEGIN) == 1


def test_dry_run_writes_nothing(repo):
    assert _run(repo, "--dry-run") == 0
    assert list(repo.iterdir()) == []


# --- refusals ------------------------------------------------------------------------


def test_refuses_the_home_directory(sandboxed_user_settings, capsys):
    home = sandboxed_user_settings.parent.parent
    assert main(["--path", str(home)]) == 1
    assert not (home / ".claude" / "hooks").exists()
    assert not (home / ".claude" / "agents").exists()
    assert "inside a project" in capsys.readouterr().out


def test_refuses_a_directory_name_that_is_not_a_valid_namespace(tmp_path, capsys):
    bad = tmp_path / "my repo!"
    bad.mkdir()
    assert main(["--path", str(bad)]) == 1
    assert list(bad.iterdir()) == []
    assert "namespace" in capsys.readouterr().out


def test_cli_routes_harness(repo, monkeypatch):
    from gingugu import server

    monkeypatch.setattr(sys, "argv", ["gingugu", "harness", "--path", str(repo), "--dry-run"])
    with pytest.raises(SystemExit) as exit_info:
        server.main()
    assert exit_info.value.code == 0
