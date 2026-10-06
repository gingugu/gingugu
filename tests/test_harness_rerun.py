"""Running ``gingugu harness`` on a repo that already has the harness's pieces.

Three things a real repo turned up, each a run that told the user something
untrue or wrote a second copy of what was already there:

- ``--prune --dry-run`` must report what the real ``--prune`` then does;
- a ``CLAUDE.md`` that already carries its own knowledge-base section gets no
  second, generic copy appended beside it;
- ``.gitignore`` rules join the block ``gingugu init`` wrote before rather than
  opening a second block under the same header.
"""

from __future__ import annotations

import json
import re

from gingugu.bootstrap import main as init_main
from gingugu.bootstrap.harness import HARNESS_BEGIN, main

RUN = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks"
HEADER = "# Claude Code / Gingugu artifacts (added by `gingugu init`)"
LEGACY = ("post_tool_use.py", "notification.py")


def _kit_repo(tmp_path):
    repo = tmp_path / "kit-repo"
    hooks = repo / ".claude" / "hooks"
    (hooks / "utils" / "tts").mkdir(parents=True)
    (hooks / "utils" / "tts" / "tts_queue.py").write_text("# kit tts\n")
    for name in LEGACY:
        (hooks / name).write_text(
            f'# kit {name}\nlog_path = os.path.join("logs", "{name[:-3]}.json")\n'
        )
    settings = {
        "hooks": {
            "PostToolUse": [{"hooks": [{"type": "command", "command": f"{RUN}/post_tool_use.py"}]}]
        }
    }
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    return repo


def _removed_count(out):
    found = re.search(r"(?:would remove|removed) (\d+) retired file", out)
    return int(found.group(1)) if found else 0


# --- prune --dry-run tells the truth --------------------------------------------------


def test_prune_dry_run_reports_what_prune_then_removes(tmp_path, capsys):
    repo = _kit_repo(tmp_path)
    assert main(["--path", str(repo), "--migrate"]) == 0
    capsys.readouterr()

    assert main(["--path", str(repo), "--prune", "--dry-run"]) == 0
    dry = capsys.readouterr().out
    assert main(["--path", str(repo), "--prune"]) == 0
    real = capsys.readouterr().out

    assert "its stub is kept" not in dry
    assert _removed_count(dry) == _removed_count(real) == len(LEGACY) + 1
    assert not (repo / ".claude" / "hooks" / "retired").exists()


def test_prune_dry_run_still_keeps_the_original_behind_a_kept_stub(tmp_path, capsys):
    repo = _kit_repo(tmp_path)
    main(["--path", str(repo), "--migrate"])
    stub = repo / ".claude" / "hooks" / "notification.py"
    stub.write_text(stub.read_text() + "# edited by the user\n")
    capsys.readouterr()

    main(["--path", str(repo), "--prune", "--dry-run"])
    dry = capsys.readouterr().out
    assert "retired/notification.py  (its stub is kept)" in dry


# --- CLAUDE.md that already has its own knowledge-base section ------------------------

OWN_SECTION = "# my repo\n\n## AI Knowledge Base Enforcement\n\nOur own, stricter rules.\n"


def test_claude_md_with_its_own_section_gets_no_second_copy(tmp_path, capsys):
    repo = tmp_path / "own-rules"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(OWN_SECTION)

    assert main(["--path", str(repo)]) == 0
    text = (repo / "CLAUDE.md").read_text()

    assert HARNESS_BEGIN not in text
    assert text.count("## AI Knowledge Base Enforcement") == 1
    assert "Our own, stricter rules." in text
    assert "has its own" in capsys.readouterr().out


def test_claude_md_own_section_dry_run_says_skip_not_append(tmp_path, capsys):
    repo = tmp_path / "own-rules"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(OWN_SECTION)
    capsys.readouterr()

    main(["--path", str(repo), "--dry-run"])
    out = capsys.readouterr().out
    assert "would append harness block" not in out
    assert "has its own" in out


def test_claude_md_with_our_block_is_still_refreshed(tmp_path):
    repo = tmp_path / "ours"
    repo.mkdir()
    main(["--path", str(repo)])
    claude = repo / "CLAUDE.md"
    claude.write_text(claude.read_text().replace("Never skip the `.ai/`", "STALE"))

    main(["--path", str(repo)])
    assert "STALE" not in claude.read_text()
    assert claude.read_text().count(HARNESS_BEGIN) == 1


# --- .gitignore joins the existing block ----------------------------------------------


def test_gitignore_adds_under_the_existing_header(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(f"node_modules/\n\n{HEADER}\nlogs/\n\n# user section\ndist/\n")

    init_main(["--path", str(tmp_path)])
    body = gitignore.read_text()

    assert body.count(HEADER) == 1
    block = body.split(HEADER)[1].split("\n\n")[0]
    assert "logs/" in block and ".claude/data/" in block and "AGENTS.md.bak" in block
    assert body.endswith("# user section\ndist/\n")
    assert body.startswith("node_modules/\n\n")


def test_gitignore_rules_go_before_a_users_negation_under_the_header(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(f"{HEADER}\nlogs/\n!logs/keep.txt\n")

    init_main(["--path", str(tmp_path)])
    lines = gitignore.read_text().splitlines()

    assert lines.count(HEADER) == 1
    assert lines[-1] == "!logs/keep.txt"  # the user's rule still wins
    assert ".claude/data/" in lines


def test_gitignore_header_at_end_of_file_without_newline(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(f"{HEADER}\nlogs/")

    init_main(["--path", str(tmp_path)])
    body = gitignore.read_text()

    assert body.count(HEADER) == 1
    assert body.startswith(f"{HEADER}\nlogs/\n")
    assert ".claude/data/\n" in body and body.endswith("\n")
