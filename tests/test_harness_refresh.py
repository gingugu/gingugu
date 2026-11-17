"""`gingugu harness` refreshes a managed file that is a stock copy of an older version.

Harness files are rendered per repo (``{{namespace}}``, ``{{grant}}``), so the
same shipped version has different bytes in every repo and a plain sha256
registry cannot recognise it. Instead every older template TEXT is kept under
``templates/harness/_shipped/``; a file on disk is stock when it equals one of
them rendered for this repo. Stock copies are refreshed on a plain run (with a
.bak); anything else is the user's and is skipped unless ``--force``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gingugu.bootstrap._files import read_template
from gingugu.bootstrap.harness import _MANAGED, main, minion_grant
from gingugu.bootstrap.harness_shipped import (
    HARNESS_PINNED,
    previous_templates,
    render,
)
from gingugu.bootstrap.shipped import fingerprint

TEMPLATES = Path(__file__).parent.parent / "src" / "gingugu" / "bootstrap" / "templates"
AGENT = "agents/repo-scout.md"
AGENT_DEST = ".claude/agents/repo-scout.md"


@pytest.mark.parametrize("rel", sorted(HARNESS_PINNED))
def test_current_template_is_pinned(rel):
    """Editing a harness template fails here until the old text is kept.

    Copy the outgoing template into ``templates/harness/_shipped/<rel>.versions/`` (named
    by its sha256 prefix) and pin the new hash. Forget it and every repo holding
    the old version would be treated as edited and never refreshed.
    """
    data = (TEMPLATES / "harness" / f"{rel}.tmpl").read_bytes()
    assert fingerprint(data) == HARNESS_PINNED[rel], (
        f"harness/{rel}.tmpl changed: save the old text under "
        f"templates/harness/_shipped/{rel}.versions/ and pin the new hash in harness_shipped.py"
    )


def test_every_managed_template_is_pinned():
    assert {t for t, _ in _MANAGED} <= set(HARNESS_PINNED)


def test_kept_versions_are_never_the_current_one():
    for rel in HARNESS_PINNED:
        for old in previous_templates(rel):
            assert fingerprint(old.encode()) != HARNESS_PINNED[rel], rel


def test_older_versions_are_kept_for_templates_that_changed():
    # These three changed between 0.22.0 and now; a repo on any of them must refresh.
    for rel in ("agents/repo-scout.md", "skills/creating-pr/SKILL.md", "hooks/log_event.py"):
        assert previous_templates(rel), rel


# --- the run itself ------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    return repo


def _old_render(rel: str, namespace: str) -> str:
    return render(previous_templates(rel)[0], namespace, minion_grant(namespace))


def test_stock_old_copy_is_refreshed_on_a_plain_run(tmp_path):
    repo = _repo(tmp_path)
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    old = _old_render(AGENT, "myrepo")
    dest.write_text(old)

    assert main(["--path", str(repo)]) == 0

    current = render(read_template(f"harness/{AGENT}.tmpl"), "myrepo", minion_grant("myrepo"))
    assert dest.read_text() == current
    assert (dest.parent / "repo-scout.md.bak").read_text() == old


def test_crlf_stock_copy_is_still_recognised(tmp_path):
    repo = _repo(tmp_path)
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    dest.write_bytes(_old_render(AGENT, "myrepo").replace("\n", "\r\n").encode())

    main(["--path", str(repo)])

    assert b"\r\n" not in dest.read_bytes()


def test_edited_copy_is_skipped_without_force(tmp_path, capsys):
    repo = _repo(tmp_path)
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    edited = _old_render(AGENT, "myrepo") + "\nMy own extra rule.\n"
    dest.write_text(edited)

    main(["--path", str(repo)])

    assert dest.read_text() == edited
    assert "exists; use --force" in capsys.readouterr().out


def test_old_copy_rendered_for_another_repo_is_not_stock(tmp_path):
    """Copied in from a different repo: its grant is wrong here, so it is the user's."""
    repo = _repo(tmp_path)
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    foreign = _old_render(AGENT, "otherrepo")
    dest.write_text(foreign)

    main(["--path", str(repo)])

    assert dest.read_text() == foreign


def test_current_copy_reports_no_change_not_a_force_hint(tmp_path, capsys):
    repo = _repo(tmp_path)
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    current = render(read_template(f"harness/{AGENT}.tmpl"), "myrepo", minion_grant("myrepo"))
    dest.write_text(current)

    main(["--path", str(repo)])

    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "repo-scout.md" in ln)
    assert "already current" in line and "--force" not in line


def test_dry_run_reports_the_refresh_and_writes_nothing(tmp_path, capsys):
    repo = _repo(tmp_path)
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    old = _old_render(AGENT, "myrepo")
    dest.write_text(old)

    main(["--path", str(repo), "--dry-run"])

    assert dest.read_text() == old
    assert not (dest.parent / "repo-scout.md.bak").exists()
    assert "stock copy of an older version" in capsys.readouterr().out


def test_namespace_word_inside_the_template_text_does_not_block_refresh(tmp_path):
    """A repo named like a word the template already uses (here `crow`)."""
    repo = tmp_path / "crow"
    repo.mkdir()
    dest = repo / AGENT_DEST
    dest.parent.mkdir(parents=True)
    dest.write_text(_old_render(AGENT, "crow"))

    main(["--path", str(repo)])

    current = render(read_template(f"harness/{AGENT}.tmpl"), "crow", minion_grant("crow"))
    assert dest.read_text() == current
