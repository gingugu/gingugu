"""`--migrate` / `--prune` only ever move or delete what is provably the old kit's.

A file name alone proves nothing: `notification.py` or `permission_request.py`
may be the user's own. A kit script is recognised by its fingerprint (it names
its own JSON-array log), everything moved into ``retired/`` is hashed into a
manifest, and prune deletes only manifest files whose bytes still match.
"""

from __future__ import annotations

import json
import os

import pytest

from gingugu.bootstrap import harness
from gingugu.bootstrap.harness import main

RUN = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks"


def kit(name):
    return f'# kit {name}\nlog_path = os.path.join("logs", "{name[:-3]}.json")\n'


def _group(*commands):
    return {"matcher": "", "hooks": [{"type": "command", "command": c} for c in commands]}


def _make(tmp_path, hooks_settings, files):
    repo = tmp_path / "safety-repo"
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
        {
            "PostToolUse": [_group(f"{RUN}/post_tool_use.py")],
            "Notification": [_group(f"{RUN}/notification.py")],
        },
        {"post_tool_use.py": kit("post_tool_use.py"), "notification.py": kit("notification.py")},
    )


# --- recognising the kit -------------------------------------------------------------


def test_a_users_own_script_with_a_kit_name_is_left_alone(tmp_path):
    mine = '# my own permission policy\nprint(\'{"decision": "deny"}\')\n'
    repo = _make(
        tmp_path,
        {"PermissionRequest": [_group(f"{RUN}/permission_request.py")]},
        {"permission_request.py": mine},
    )
    _run(repo, "--migrate")
    assert (repo / ".claude" / "hooks" / "permission_request.py").read_text() == mine
    assert f"{RUN}/permission_request.py" in _commands(repo)


def test_a_kit_name_outside_the_hooks_dir_is_not_unwired(tmp_path):
    repo = _make(tmp_path, {"Notification": [_group("uv run /opt/team/notification.py")]}, {})
    _run(repo, "--migrate")
    assert "uv run /opt/team/notification.py" in _commands(repo)


def test_only_the_kits_own_utils_files_move(tmp_path):
    repo = _make(
        tmp_path,
        {},
        {"utils/tts/tts_queue.py": "# kit\n", "utils/mine.py": "# mine\n"},
    )
    _run(repo, "--migrate")
    hooks = repo / ".claude" / "hooks"
    assert (hooks / "utils" / "mine.py").read_text() == "# mine\n"
    assert (hooks / "retired" / "utils" / "tts" / "tts_queue.py").exists()


# --- prune deletes only what the manifest proves is ours ---------------------------------


def test_prune_leaves_a_retired_directory_it_did_not_create(tmp_path):
    repo = _make(tmp_path, {}, {"retired/my-archive.py": "# keep me\n"})
    _run(repo, "--prune")
    assert (repo / ".claude" / "hooks" / "retired" / "my-archive.py").read_text() == "# keep me\n"


def test_prune_keeps_a_retired_original_edited_after_migrate(repo):
    _run(repo, "--migrate")
    original = repo / ".claude" / "hooks" / "retired" / "post_tool_use.py"
    original.write_text(original.read_text() + "# I changed this\n")
    _run(repo, "--prune")
    assert original.exists()


def test_prune_leaves_a_file_added_to_retired_after_migrate(repo):
    _run(repo, "--migrate")
    extra = repo / ".claude" / "hooks" / "retired" / "notes.txt"
    extra.write_text("mine\n")
    _run(repo, "--prune")
    assert extra.read_text() == "mine\n"
    assert not (repo / ".claude" / "hooks" / "retired" / "post_tool_use.py").exists()


def test_prune_honours_wiring_in_settings_local_json(repo):
    _run(repo, "--migrate")
    local = {"hooks": {"Notification": [_group(f"{RUN}/notification.py")]}}
    (repo / ".claude" / "settings.local.json").write_text(json.dumps(local))
    _run(repo, "--prune")
    hooks = repo / ".claude" / "hooks"
    assert (hooks / "notification.py").exists()
    assert (hooks / "retired" / "notification.py").exists()


def test_prune_sees_a_script_named_without_the_hooks_path(repo):
    _run(repo, "--migrate")
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    settings["hooks"]["Notification"] = [_group("cd .claude/hooks && python notification.py")]
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    _run(repo, "--prune")
    assert (repo / ".claude" / "hooks" / "notification.py").exists()


# --- symlinks ------------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_migrate_never_writes_through_a_symlinked_hook(repo, tmp_path):
    shared = tmp_path / "shared-stop.py"
    shared.write_text("# shared across repos\n")
    (repo / ".claude" / "hooks" / "stop.py").symlink_to(shared)
    _run(repo, "--migrate")
    assert shared.read_text() == "# shared across repos\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_prune_never_follows_a_symlinked_retired_dir(repo, tmp_path):
    _run(repo, "--migrate")
    retired = repo / ".claude" / "hooks" / "retired"
    outside = tmp_path / "outside"
    retired.rename(outside)
    retired.symlink_to(outside, target_is_directory=True)
    _run(repo, "--prune")
    assert (outside / "post_tool_use.py").exists()


# --- settings safety -------------------------------------------------------------------------


def test_settings_backup_exists_before_a_later_step_fails(repo, monkeypatch):
    original = (repo / ".claude" / "settings.json").read_text()

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(harness, "_scaffold_ai", boom)
    with pytest.raises(RuntimeError):
        _run(repo, "--migrate")
    assert (repo / ".claude" / "settings.json.bak").read_text() == original


def test_migrate_refuses_settings_that_are_not_a_json_object(repo, capsys):
    (repo / ".claude" / "settings.json").write_text("[]")
    assert _run(repo, "--migrate") == 1
    assert not (repo / "CLAUDE.md").exists()
    assert (repo / ".claude" / "hooks" / "post_tool_use.py").read_text() == kit("post_tool_use.py")
    assert "settings.json" in capsys.readouterr().out
