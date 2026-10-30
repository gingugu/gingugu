"""The checkout guard: no session moves the shared primary checkout.

Several agent sessions routinely start in the same repo's primary checkout. A
``git switch`` in one moves the branch under every other, and their
uncommitted edits ride along onto it; a ``git stash`` or ``reset --hard``
takes those edits away outright. A rule in prose did not stop it, so this is a
gate: inside ``gingugu hook tool`` (PreToolUse), a Bash call that would move or
empty the PRIMARY checkout is denied with a pointer to a worktree.

Primary means ``git rev-parse --git-dir`` equals ``--git-common-dir``. A linked
worktree - including a minion's ``isolation: worktree`` - is never guarded.
Commits, pull, push and every read still pass: on the current branch they
change nothing another session did not already see.

Parsing is deliberately shallow: heredoc bodies dropped, then a quote-aware
``shlex`` split on shell separators. Only a segment whose first word (after
``VAR=value`` prefixes) is ``git`` is looked at, so a ``git switch`` quoted in
``echo``, ``grep`` or a commit message is not a git call. A ``cd`` segment
moves where the later ones run, and ``-C`` too.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

OVERRIDE = "GINGUGU_PRIMARY_OK"

# Characters that end a simple command. ``_SEPARATORS`` is the blunt fallback
# for a command whose quotes do not close.
_PUNCTUATION = ";&|\n"
_SEPARATORS = re.compile(r"[;&|\n]")

# A heredoc's body (``<<EOF`` / ``<<-'EOF'`` up to the delimiter line) is text
# fed to a command, never commands of this shell.
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n(?:.*?\n)??[ \t]*\2[ \t]*(?=\n|$)", re.S)

# Global options that take a value as the next word.
_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

_RESET_MOVES = {"--hard", "--merge", "--keep"}
_STASH_READS = {"list", "show"}
_REBASE_EXITS = {"--abort", "--quit"}


def enabled() -> bool:
    return os.environ.get("MEMORY_CHECKOUT_GUARD", "on").lower() not in ("0", "off", "false")


def _segments(command: str) -> list[list[str]]:
    """The command's simple commands, as word lists.

    Separators inside quotes and heredoc bodies are prose, not commands: a
    commit message that mentions ``git switch`` must not be read as one. On a
    quote ``shlex`` cannot close, fall back to a blunt split per separator.
    """
    command = _HEREDOC.sub("", command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return [segment.split() for segment in _SEPARATORS.split(command)]
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= set(_PUNCTUATION):
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _moves_checkout(sub: str, args: list[str]) -> bool:
    """True when ``git <sub> <args>`` moves or empties the working tree."""
    if sub == "switch":
        return True
    if sub == "checkout":
        # `checkout -- path` / `checkout <rev> -- path` restores files in place.
        return "--" not in args
    if sub == "rebase":
        return not _REBASE_EXITS & set(args)
    if sub == "reset":
        return bool(_RESET_MOVES & set(args))
    if sub == "stash":
        return not args or args[0] not in _STASH_READS
    return False


def _git_call(words: list[str], cwd: Path) -> tuple[str, Path] | None:
    """``(rendered command, directory)`` for a guarded git call, else None."""
    env: dict[str, str] = {}
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        key, _, value = words.pop(0).partition("=")
        env[key] = value
    if not words or os.path.basename(words[0]) != "git":
        return None
    if env.get(OVERRIDE) == "1":
        return None
    i = 1
    while i < len(words) and words[i].startswith("-"):
        flag = words[i]
        if flag in _GLOBAL_WITH_VALUE and i + 1 < len(words):
            if flag == "-C":
                cwd = cwd / words[i + 1]
            i += 2
            continue
        i += 1
    if i >= len(words):
        return None
    sub, args = words[i], words[i + 1 :]
    if not _moves_checkout(sub, args):
        return None
    return " ".join(["git", sub, *args]), cwd


def _primary_top(directory: Path) -> Path | None:
    """The checkout's top level when ``directory`` is in a PRIMARY checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"]
            + ["--show-toplevel"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) != 3:
        return None
    git_dir, common_dir, top = lines
    if Path(git_dir).resolve() != Path(common_dir).resolve():
        return None
    return Path(top).resolve()


def _reason(command: str, top: Path) -> str:
    return (
        f"gingugu checkout guard: `{command}` would move or empty the PRIMARY "
        f"checkout at {top}. Other sessions may be working in it, and their "
        "uncommitted edits would ride along to your branch or be taken away.\n"
        "Do branch work in your own worktree instead:\n"
        '  - Claude Code: EnterWorktree(name="<name>") - creates '
        ".claude/worktrees/<name> and moves this session into it\n"
        "  - any agent: git worktree add .claude/worktrees/<name> -b <branch>, "
        "then work from that directory\n"
        "Reads, commits, pull and push on the current branch still pass here. "
        "Only if the user confirms no other session is in this checkout, re-run "
        f"the command prefixed with {OVERRIDE}=1."
    )


def check(payload: dict) -> str | None:
    """The deny reason for this tool call, or None to let it through."""
    if payload.get("tool_name") != "Bash" or not enabled():
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or "git" not in command:
        return None
    cwd = Path(payload.get("cwd") or os.getcwd())
    for words in _segments(command):
        if len(words) >= 2 and words[0] == "cd":
            cwd = cwd / os.path.expanduser(words[1])
            continue
        call = _git_call(words, cwd)
        if call is None:
            continue
        rendered, directory = call
        top = _primary_top(directory)
        if top is not None:
            return _reason(rendered, top)
    return None
