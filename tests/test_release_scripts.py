"""The pure parts of scripts/release.py and scripts/fleet.py.

The side effects (git, gh, ssh, PyPI) are the scripts' job and are exercised
by running them; what is pinned here is the logic a release depends on being
exactly right every time: the CHANGELOG roll, the version bump, the guards,
and the commands sent to each machine.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


release = _load("release")
fleet = _load("fleet")

_CHANGELOG = """# Changelog

---

## [Unreleased]

### Changed

- A thing.

---

## [0.23.0] - 2026-10-17

### Added

- Older.
"""


def test_the_changelog_roll_moves_unreleased_under_the_version():
    rolled = release.roll_changelog(_CHANGELOG, "0.24.0", "2026-10-18")
    assert (
        "## [Unreleased]\n\n---\n\n## [0.24.0] - 2026-10-18\n\n### Changed\n\n- A thing.\n"
        in rolled
    )
    assert rolled.index("## [0.24.0]") < rolled.index("## [0.23.0]")
    assert rolled.endswith("- Older.\n")


def test_an_empty_unreleased_is_refused():
    empty = _CHANGELOG.replace("### Changed\n\n- A thing.\n", "")
    with pytest.raises(release.ReleaseError, match="empty"):
        release.roll_changelog(empty, "0.24.0", "2026-10-18")


def test_a_version_already_in_the_changelog_is_refused():
    with pytest.raises(release.ReleaseError, match="already"):
        release.roll_changelog(_CHANGELOG, "0.23.0", "2026-10-18")


def test_the_bump_touches_only_the_project_version():
    pyproject = 'name = "gingugu"\nversion = "0.23.0"\n[tool.x]\nversion = "9.9.9"\n'
    bumped = release.bump_pyproject(pyproject, "0.24.0")
    assert release.current_version(bumped) == "0.24.0"
    assert 'version = "9.9.9"' in bumped


@pytest.mark.parametrize("bad", ["0.24", "v0.24.0", "0.24.0rc1", ""])
def test_only_plain_semver_is_accepted(bad):
    with pytest.raises(release.ReleaseError):
        release.parse_version(bad)


def test_versions_compare_numerically_not_as_text():
    assert release.parse_version("0.10.0") > release.parse_version("0.9.9")


def test_the_release_state_must_name_the_new_version():
    release.check_release_state("- Current version: **0.24.0** (PyPI)", "0.24.0")
    with pytest.raises(release.ReleaseError, match="Release State"):
        release.check_release_state("- Current version: **0.23.0** (PyPI)", "0.24.0")


def _fleet_file(tmp_path, machines) -> Path:
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps({"machines": machines}))
    return path


def test_a_service_machine_installs_as_its_user_with_its_extras(tmp_path):
    (pi,) = fleet.load(
        _fleet_file(
            tmp_path,
            [{"name": "pi", "ssh": "me@pi", "run_as": "svc", "with": ["keyrings.alt"]}],
        )
    )
    command = fleet.install_command(pi, "0.24.0")
    assert command.startswith("sudo -n -u svc -H bash -c ")
    assert "tool install --force --refresh gingugu==0.24.0 --with keyrings.alt" in command


def test_a_plain_machine_runs_as_the_ssh_user(tmp_path):
    (mac,) = fleet.load(_fleet_file(tmp_path, [{"name": "mac", "uv": "/opt/homebrew/bin/uv"}]))
    command = fleet.install_command(mac, "0.24.0")
    assert command.startswith("bash -c ") and "sudo" not in command
    assert "/opt/homebrew/bin/uv tool install" in command


def test_duplicate_machine_names_are_refused(tmp_path):
    with pytest.raises(fleet.FleetError, match="duplicate"):
        fleet.load(_fleet_file(tmp_path, [{"name": "a"}, {"name": "a"}]))


def test_a_missing_fleet_file_says_where_it_looked(tmp_path):
    with pytest.raises(fleet.FleetError, match="no fleet file"):
        fleet.load(tmp_path / "absent.json")


# --- fleet status: each machine's remote URL and reachability ---------------------
#
# A machine left pointing at a stale URL (http after the brain went TLS-only)
# was found only when its session failed to connect. `status` now shows what
# each machine's `gingugu remote status` says, and fails when one cannot reach.

_REMOTE_OK = "remote: https://brain.local:8765\nsource: machine\ntoken: present\nreachable: yes"
_REMOTE_TLS = (
    "remote: http://brain.local:8765\nsource: machine\ntoken: present\nreachable: no\n"
    "hint: the brain at brain.local:8765 speaks TLS; run "
    "`gingugu remote login https://brain.local:8765`"
)


def test_parse_remote_status_local():
    assert fleet.parse_remote_status("local") == {"mode": "local"}


def test_parse_remote_status_reachable():
    assert fleet.parse_remote_status(_REMOTE_OK) == {
        "mode": "remote",
        "remote": "https://brain.local:8765",
        "source": "machine",
        "token": "present",
        "reachable": "yes",
    }


def test_parse_remote_status_keeps_the_hint_whole():
    # The hint carries a URL with its own colons; only the first ": " splits.
    parsed = fleet.parse_remote_status(_REMOTE_TLS)
    assert parsed["reachable"] == "no"
    assert parsed["hint"].endswith("`gingugu remote login https://brain.local:8765`")


@pytest.mark.parametrize("text", ["", "gingugu: command not found", "Usage: gingugu remote ..."])
def test_parse_remote_status_of_anything_else_is_unknown(text):
    # An older gingugu, a missing binary, or a failed ssh: never guessed at.
    assert fleet.parse_remote_status(text) == {"mode": "unknown"}


def test_remote_status_asks_the_install_user(tmp_path, monkeypatch):
    (pi,) = fleet.load(_fleet_file(tmp_path, [{"name": "pi", "ssh": "me@pi", "run_as": "svc"}]))
    sent = []

    def fake_shell(machine, command, *, check=True):
        sent.append((command, check))
        return "local"

    monkeypatch.setattr(fleet, "shell", fake_shell)
    assert fleet.remote_status(pi) == {"mode": "local"}
    ((command, check),) = sent
    assert command == fleet.as_user(pi, "gingugu remote status")
    assert check is False  # a failing machine is reported, not fatal


def test_status_line_local():
    assert fleet.status_line("pi", "0.24.0", {"mode": "local"}) == "pi: 0.24.0, local"


def test_status_line_reachable():
    line = fleet.status_line("mac", "0.24.0", fleet.parse_remote_status(_REMOTE_OK))
    assert line == "mac: 0.24.0, remote https://brain.local:8765, reachable"


def test_status_line_unreachable_carries_the_hint():
    line = fleet.status_line("mac", "0.24.0", fleet.parse_remote_status(_REMOTE_TLS))
    assert line.startswith("mac: 0.24.0, remote http://brain.local:8765, UNREACHABLE - ")
    assert "speaks TLS" in line


def test_status_line_unreachable_without_a_hint():
    status = {"mode": "remote", "remote": "https://b:1", "reachable": "no"}
    line = fleet.status_line("mac", "0.24.0", status)
    assert line == "mac: 0.24.0, remote https://b:1, UNREACHABLE"


def test_status_line_unknown():
    line = fleet.status_line("old", "0.19.0", {"mode": "unknown"})
    assert line == "old: 0.19.0, remote status unknown"


def _status_run(tmp_path, monkeypatch, capsys, statuses: dict[str, str]) -> tuple[int, str]:
    path = _fleet_file(tmp_path, [{"name": name} for name in statuses])
    monkeypatch.setenv("GINGUGU_FLEET", str(path))
    monkeypatch.setattr(fleet, "installed", lambda machine: "0.24.0")
    monkeypatch.setattr(
        fleet, "remote_status", lambda machine: fleet.parse_remote_status(statuses[machine.name])
    )
    code = fleet.main(["status"])
    return code, capsys.readouterr().out


def test_status_prints_one_line_per_machine_and_passes_when_all_reach(
    tmp_path, monkeypatch, capsys
):
    code, out = _status_run(tmp_path, monkeypatch, capsys, {"pi": "local", "mac": _REMOTE_OK})
    assert code == 0
    assert out.splitlines() == [
        "pi: 0.24.0, local",
        "mac: 0.24.0, remote https://brain.local:8765, reachable",
    ]


def test_status_fails_when_any_machine_cannot_reach_its_brain(tmp_path, monkeypatch, capsys):
    code, out = _status_run(tmp_path, monkeypatch, capsys, {"pi": "local", "mac": _REMOTE_TLS})
    assert code == 1
    assert len(out.splitlines()) == 2  # every machine is still reported
    assert "UNREACHABLE" in out


def test_status_does_not_fail_on_an_unknown_remote_status(tmp_path, monkeypatch, capsys):
    # Unknown is reported, not treated as an outage: it may be an older gingugu.
    code, _ = _status_run(tmp_path, monkeypatch, capsys, {"old": ""})
    assert code == 0
