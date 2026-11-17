"""``gingugu harness`` sets the default subagent model in the user-level settings.

The repo ``permissions.deny`` of ``Agent(model:opus)`` only stops a spawn that
NAMES Opus. Built-in subagents (Explore, Plan, general-purpose) reach Opus by
inheriting the main thread's model when no model is passed. The
``CLAUDE_CODE_SUBAGENT_MODEL`` env var is the default for any subagent whose
definition names none, so it closes that path; agents that pin a model keep it.
"""

from __future__ import annotations

import json

from gingugu.bootstrap.harness import main
from gingugu.bootstrap.harness_settings import (
    SUBAGENT_MODEL,
    SUBAGENT_MODEL_ENV,
    init_user_subagent_model,
)


def test_sets_the_env_var_in_a_missing_file(tmp_path):
    path = tmp_path / ".claude" / "settings.json"

    out = "\n".join(init_user_subagent_model(dry_run=False, path=path))

    assert json.loads(path.read_text())["env"][SUBAGENT_MODEL_ENV] == SUBAGENT_MODEL == "sonnet"
    assert "set" in out


def test_adds_to_existing_env_and_backs_up(tmp_path):
    path = tmp_path / "settings.json"
    original = json.dumps({"env": {"MEMORY_PERSONA": "beepboop"}, "model": "opus"})
    path.write_text(original)

    init_user_subagent_model(dry_run=False, path=path)

    data = json.loads(path.read_text())
    assert data["env"] == {"MEMORY_PERSONA": "beepboop", SUBAGENT_MODEL_ENV: "sonnet"}
    assert data["model"] == "opus"
    assert (tmp_path / "settings.json.bak").read_text() == original


def test_idempotent_no_rewrite(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"env": {SUBAGENT_MODEL_ENV: "sonnet"}}))
    before = path.read_text()

    out = "\n".join(init_user_subagent_model(dry_run=False, path=path))

    assert path.read_text() == before
    assert not (tmp_path / "settings.json.bak").exists()
    assert "no change" in out


def test_never_overrides_a_value_the_user_chose(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"env": {SUBAGENT_MODEL_ENV: "haiku"}}))
    before = path.read_text()

    out = "\n".join(init_user_subagent_model(dry_run=False, path=path))

    assert path.read_text() == before
    assert "haiku" in out and "kept" in out


def test_unparseable_file_is_never_touched(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json")

    out = "\n".join(init_user_subagent_model(dry_run=False, path=path))

    assert path.read_text() == "{ not json"
    assert "WARNING" in out


def test_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}")

    out = "\n".join(init_user_subagent_model(dry_run=True, path=path))

    assert path.read_text() == "{}"
    assert "would set" in out


def test_harness_run_wires_it_in_the_sandboxed_user_settings(tmp_path, sandboxed_user_settings):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["--path", str(repo)]) == 0

    env = json.loads(sandboxed_user_settings.read_text())["env"]
    assert env[SUBAGENT_MODEL_ENV] == "sonnet"
