"""`gingugu init` refreshes a hook that is a stock copy of an older template.

A hook byte-identical to a version we shipped holds nothing of the user's, so
plain `init` replaces it (with a .bak, as every overwrite does). Anything else
is treated as the user's edits and skipped unless `--force`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gingugu.bootstrap import main
from gingugu.bootstrap._files import read_template
from gingugu.bootstrap.shipped import SHIPPED, previous_versions

TEMPLATES = Path(__file__).parent.parent / "src" / "gingugu" / "bootstrap" / "templates"
DOORWAY = "pre_tool_tripwire.py"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _old_doorway() -> str:
    """The doorway 0.47.x shipped, before the big-delete gate (8cf3d9b^).

    A fixture, not `git show`: CI clones shallow.
    """
    return (Path(__file__).parent / "fixtures" / "pre_tool_tripwire_0.47.py.txt").read_text()


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_current_hash_is_pinned(name):
    """Editing a template fails here until its old hash moves to `previous`.

    That is the point: without it, the outgoing version would never be
    recognised as stock and `init` would skip it forever.
    """
    assert _sha(read_template(name)) == SHIPPED[name]["current"], (
        f"{name} changed: add the old `current` hash to its `previous` set in "
        f"bootstrap/shipped.py and pin the new one"
    )


def test_every_installed_hook_template_is_registered():
    hook_templates = {p.name for p in TEMPLATES.glob("*.py.tmpl")}
    assert hook_templates <= set(SHIPPED)


def test_current_is_never_listed_as_previous():
    for name, entry in SHIPPED.items():
        assert entry["current"] not in entry["previous"], name


def test_known_old_doorway_is_registered():
    assert _sha(_old_doorway()) in previous_versions(f"{DOORWAY}.tmpl")


def test_plain_init_refreshes_a_stock_old_doorway(tmp_path):
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    old = _old_doorway()
    (hooks / DOORWAY).write_text(old)

    assert main(["--path", str(tmp_path)]) == 0

    assert (hooks / DOORWAY).read_text() == read_template(f"{DOORWAY}.tmpl")
    assert (hooks / f"{DOORWAY}.bak").read_text() == old


def test_plain_init_skips_an_edited_doorway(tmp_path):
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    edited = _old_doorway() + "\n# my own line\n"
    (hooks / DOORWAY).write_text(edited)

    assert main(["--path", str(tmp_path)]) == 0

    assert (hooks / DOORWAY).read_text() == edited
    assert not (hooks / f"{DOORWAY}.bak").exists()


def test_dry_run_reports_the_refresh_and_writes_nothing(tmp_path, capsys):
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    old = _old_doorway()
    (hooks / DOORWAY).write_text(old)

    assert main(["--path", str(tmp_path), "--dry-run"]) == 0

    assert (hooks / DOORWAY).read_text() == old
    assert not (hooks / f"{DOORWAY}.bak").exists()
    assert "stock copy of an older version" in capsys.readouterr().out
