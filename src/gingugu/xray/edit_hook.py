"""X-ray on edit: the first edit of an imported source file names its dependents.

Called from ``gingugu hook tool`` (PreToolUse). Deny is the only lever that
speaks before a call runs, so the first ``Edit``/``Write``/``MultiEdit`` of a
file that other files import is denied with its blast radius as the reason; the
re-issued call passes. The graph is built here from the checkout and cached
per repo by fingerprint, so it never needs the brain.

Like every hook it must never break a session: bad input, a non-repo path or
an extraction failure all return None (stay silent).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import bash_state, edit_symbols, hook_common, lang, rank
from .hook_common import _claim, _show, _sweep
from .scan import XrayError

TOP_N = 5
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_TRIPWIRE", "on").lower() not in ("0", "off", "false")


def _target(payload: dict) -> tuple[Path, str] | None:
    """(repo root, repo-relative path), or None when this call is not ours."""
    if payload.get("tool_name") not in EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw or lang.for_path(raw) is None:
        return None
    return hook_common.resolve_file(raw, payload.get("cwd"))


def _claim_path(state_dir: Path, session_id: str, root: Path, rel: str) -> Path:
    """One file per (session, repo, file): its existence is "already shown".

    A file per claim rather than one list per session, because hooks run in
    parallel: two read-modify-writes of a shared list lose one record, and the
    lost file is denied again on its retry."""
    return hook_common.claim_path(state_dir, "xray-shown", session_id, root, rel)


def _dependents(rel: str, blast: dict) -> list[str]:
    """The lines naming what imports ``rel``; shared with the Bash X-ray."""
    code, tests = blast["code"], blast["tests"]
    code_total, tests_total = blast["code_total"], blast["tests_total"]
    n_files = code_total + tests_total
    noun = "candidate test" if tests_total == 1 else "candidate tests"
    lines = [
        f"{_show(rel)} is imported by {n_files} file{'' if n_files == 1 else 's'} "
        f"({tests_total} {noun})."
    ]
    if code:
        lines.append("Top dependents by rank:")
        for row in code[:TOP_N]:
            hop = row["hops"]
            lines.append(f"- {_show(row['path'])} ({hop} hop{'' if hop == 1 else 's'})")
        if code_total > TOP_N:
            lines.append(f"... and {code_total - TOP_N} more")
    if tests:
        # Rank says little about tests: fixtures and helpers outrank them. List
        # what can be run, nearest the edit first.
        tests = sorted(
            tests,
            key=lambda r: (
                not r["path"].rsplit("/", 1)[-1].startswith("test_"),
                r["hops"],
                r["path"],
            ),
        )
        listed = ", ".join(_show(row["path"]) for row in tests[:TOP_N])
        more = f" (+{tests_total - TOP_N} more)" if tests_total > TOP_N else ""
        lines.append(f"Candidate tests: {listed}{more}")
    return lines


def _render(rel: str, blast: dict, symbol_lines: list[str] | None = None) -> str:
    lines = _dependents(rel, blast)
    lines[0] = "GINGUGU X-RAY: " + lines[0]
    lines.extend(symbol_lines or [])
    lines.append(
        "Import-based estimate: strings, fixtures and subprocesses are not edges. "
        "Check the callers, then re-issue the edit unchanged - the X-ray fires "
        "once per file per session."
    )
    return "\n".join(lines)


def _without_seed(blast: dict, rel: str) -> dict:
    """``rank.blast`` lists a test seed as its own candidate (hops 0); for a
    deny that is no dependent, and a lone test would warn about itself."""
    tests = [r for r in blast["tests"] if r["path"] != rel]
    dropped = len(blast["tests"]) - len(tests)
    return {**blast, "tests": tests, "tests_total": blast["tests_total"] - dropped}


def check(payload: dict, state_dir: Path) -> str | None:
    """A deny reason for the first edit of an imported file, else None."""
    try:
        target = _target(payload)
        if target is None:
            return None
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None  # a shared "unknown" would let one session silence another
        root, rel = target
        bash_state.remember(state_dir, session_id, root)
        claim = _claim_path(state_dir, session_id, root, rel)
        if claim.exists():
            return None
        files, links, ranks, packages, _, _ = hook_common.graph(root, state_dir, session_id)
        if rel not in files or rel in packages:
            return None
        blast = _without_seed(rank.blast([rel], files, links, ranks, hidden=packages), rel)
        if blast["code_total"] + blast["tests_total"] == 0:
            return None
        # Deny only once the claim is on disk, and only for the hook that made it.
        if not _claim(claim):
            return None
        _sweep(state_dir)
        try:
            symbol_lines = edit_symbols.lines(payload, root, rel, state_dir, session_id)
        except (XrayError, ValueError, OSError):
            symbol_lines = []  # the deny still fires; only the symbol lines are lost
        return _render(rel, blast, symbol_lines)
    except (XrayError, ValueError, OSError):
        return None
