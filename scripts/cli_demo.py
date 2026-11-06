"""Play every CLI effect, or capture one static frame per colour level.

    uv run python scripts/cli_demo.py              # the full show, as init would play it
    uv run python scripts/cli_demo.py --party 20   # the cracktro for 20 seconds
    uv run python scripts/cli_demo.py --ladder     # one static report per level:
                                                   #   truecolor, 256, 16, ascii, plain
    uv run python scripts/cli_demo.py --capture F  # write the ladder (with escapes) to F

Nothing is written to any repo or brain: the results are a canned sample.
GINGUGU_QUIET=1 mutes the music for any of these.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import sys

from gingugu import tui
from gingugu.tui import caps as capsmod
from gingugu.tui import party

SAMPLE = [
    "Claude Code bootstrap:",
    "  write     /home/you/repo/.claude/hooks/session_start.py",
    "  wired     SessionStart + UserPromptSubmit hooks in .claude/settings.json",
    "  skip      /home/you/repo/CLAUDE.md  (has its own memory protocol)",
    "  appended  memory protocol to /home/you/repo/AGENTS.md",
    "",
    "Claude Code harness:",
    "  write     /home/you/repo/.claude/agents/repo-scout.md",
    "  write     /home/you/repo/.claude/agents/researcher.md",
    "  kept      /home/you/repo/.ai/plans/status.md  (exists; never overwritten)",
    "  WARNING: .claude/settings.local.json is not JSON; left as is",
    "",
    "Next steps:",
    "  restart Claude Code in /home/you/repo so it loads the hooks",
]

LADDER = [
    ("truecolor", capsmod.TRUECOLOR, True),
    ("256 colours", capsmod.C256, True),
    ("16 colours (CGA)", capsmod.C16, True),
    ("16 colours, ASCII glyphs (cp437 / LANG=C)", capsmod.C16, False),
    ("plain (pipe, CI, NO_COLOR, TERM=dumb)", capsmod.PLAIN, False),
]


def ladder(stream: io.TextIOBase) -> None:
    base = capsmod.detect()
    for name, level, uni in LADDER:
        caps = dataclasses.replace(base, color=level, unicode=uni, animate=False, keys=False)
        stream.write(f"\n===== {name} =====\n")
        tui.report(SAMPLE, dry_run=False, command="init", subject="repo", stream=stream, caps=caps)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--party", type=float, metavar="SECONDS")
    ap.add_argument("--ladder", action="store_true")
    ap.add_argument("--capture", metavar="FILE")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.capture:
        with open(args.capture, "w", encoding="utf-8") as fh:
            ladder(fh)
        return 0
    if args.ladder:
        ladder(sys.stdout)
        return 0
    if args.party is not None:
        return party.main([str(args.party)])
    tui.report(SAMPLE, dry_run=args.dry_run, command="init", subject="repo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
