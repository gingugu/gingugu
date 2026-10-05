"""Hostile and odd inputs to `--migrate` / `--prune`: second security pass.

A cloned repo can ship symlinks and a planted manifest. Nothing here may write
or delete outside `.claude/hooks/`, and only this project's own hook paths are
ever unwired or rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from gingugu.bootstrap.harness import main

RUN = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks"
posix_only = pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")


def kit(name):
    return f'# kit {name}\nlog_path = os.path.join("logs", "{name[:-3]}.json")\n'


def _group(*commands):
    return {"matcher": "", "hooks": [{"type": "command", "command": c} for c in commands]}


def _make(tmp_path, hooks_settings, files):
    repo = tmp_path / "hostile-repo"
    hooks = repo / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for rel, body in files.items():
        (hooks / rel).parent.mkdir(parents=True, exist_ok=True)
        (hooks / rel).write_text(body)
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": hooks_settings}))
    return repo


def _commands(repo):
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    return [h["command"] for gs in settings["hooks"].values() for g in gs for h in g["hooks"]]


def _run(repo, *flags):
    return main(["--path", str(repo), *flags])


@pytest.fixture
def repo(tmp_path):
    return _make(
        tmp_path,
        {"Notification": [_group(f"{RUN}/notification.py")]},
        {
            "notification.py": kit("notification.py"),
            "post_tool_use.py": kit("post_tool_use.py"),
            "utils/tts/tts_queue.py": "# kit tts\n",
        },
    )


# --- the manifest cannot be turned against the user -----------------------------------


@posix_only
def test_migrate_never_writes_through_a_symlinked_manifest(repo, tmp_path):
    victim = tmp_path / "dotfile"
    victim.write_text("precious\n")
    retired = repo / ".claude" / "hooks" / "retired"
    retired.mkdir()
    (retired / ".gingugu-manifest.json").symlink_to(victim)
    _run(repo, "--migrate")
    assert victim.read_text() == "precious\n"


def test_prune_ignores_manifest_entries_that_are_not_kit_files(repo):
    _run(repo, "--migrate")
    victim = repo / "LICENSE"
    victim.write_text("license text\n")
    digest = hashlib.sha256(victim.read_bytes()).hexdigest()
    manifest_path = repo / ".claude" / "hooks" / "retired" / ".gingugu-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["../../../LICENSE"] = digest
    manifest[str(victim)] = digest
    manifest_path.write_text(json.dumps(manifest))
    _run(repo, "--prune")
    assert victim.read_text() == "license text\n"


@posix_only
def test_prune_never_follows_a_symlinked_folder_inside_retired(repo, tmp_path):
    _run(repo, "--migrate")
    retired_utils = repo / ".claude" / "hooks" / "retired" / "utils"
    outside = tmp_path / "outside-utils"
    retired_utils.rename(outside)
    retired_utils.symlink_to(outside, target_is_directory=True)
    _run(repo, "--prune")
    assert (outside / "tts" / "tts_queue.py").exists()


# --- only this project's own hook paths -----------------------------------------------------


def test_reset_leaves_a_foreign_stop_py_alone(tmp_path):
    mine = "python ~/tools/stop.py --mine"
    repo = _make(tmp_path, {"Stop": [_group(mine)]}, {})
    _run(repo, "--migrate")
    assert mine in _commands(repo)


def test_unwire_leaves_a_user_level_hook_path(tmp_path):
    user_level = "uv run ~/.claude/hooks/notification.py"
    repo = _make(tmp_path, {"Notification": [_group(user_level)]}, {})
    _run(repo, "--migrate")
    assert user_level in _commands(repo)


def test_unwire_keeps_a_command_that_also_runs_the_users_own_script(tmp_path):
    mixed = f"uv run {RUN.split()[-1]}/notification.py; uv run {RUN.split()[-1]}/mine.py"
    repo = _make(
        tmp_path,
        {"Notification": [_group(mixed)]},
        {"notification.py": kit("notification.py"), "mine.py": "# mine\n"},
    )
    _run(repo, "--migrate")
    assert mixed in _commands(repo)


# --- symlinks under --force, and what a kept file protects -----------------------------------


@posix_only
def test_migrate_force_never_writes_through_a_symlinked_hook(repo, tmp_path):
    shared = tmp_path / "shared-stop.py"
    shared.write_text("# shared\n")
    (repo / ".claude" / "hooks" / "stop.py").symlink_to(shared)
    _run(repo, "--migrate", "--force")
    assert shared.read_text() == "# shared\n"


def test_prune_keeps_the_original_behind_a_replaced_stub(repo):
    _run(repo, "--migrate")
    (repo / ".claude" / "hooks" / "notification.py").write_text("# my own now\n")
    _run(repo, "--prune")
    assert (repo / ".claude" / "hooks" / "retired" / "notification.py").exists()


def test_utils_stay_when_the_users_own_kit_named_script_imports_them(tmp_path):
    repo = _make(
        tmp_path,
        {},
        {"notification.py": "from utils.tts import tts_queue\n", "utils/tts/tts_queue.py": "#\n"},
    )
    _run(repo, "--migrate")
    assert (repo / ".claude" / "hooks" / "utils" / "tts" / "tts_queue.py").exists()
