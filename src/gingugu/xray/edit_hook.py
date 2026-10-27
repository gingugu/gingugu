"""X-ray on edit: the first edit of an imported ``.py`` file names its dependents.

Called from ``gingugu hook tool`` (PreToolUse). Deny is the only lever that
speaks before a call runs, so the first ``Edit``/``Write``/``MultiEdit`` of a
file that other files import is denied with its blast radius as the reason; the
re-issued call passes. The graph is built here from the checkout and cached
per repo by fingerprint, so it never needs the brain.

Like every hook it must never break a session: bad input, a non-repo path or
an extraction failure all return None (stay silent).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import extract, local, rank
from .scan import XrayError

TOP_N = 5
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
_STATE_TTL_SECONDS = 7 * 24 * 3600


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_TRIPWIRE", "on").lower() not in ("0", "off", "false")


def _write_json(path: Path, data: object) -> bool:
    """Owner-only, written whole to a fresh temp file and renamed into place,
    so a parallel hook never reads half a file and a link planted at the temp
    name is refused. False when it could not be written."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data))
        os.replace(tmp, path)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)
        return False


def _sweep(state_dir: Path) -> None:
    """Drop X-ray state untouched for a week, as ``tool_hook`` does for tripwires."""
    import shutil

    cutoff = time.time() - _STATE_TTL_SECONDS
    try:
        for entry in state_dir.glob("xray-*"):
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def _show(path: str) -> str:
    """A repo path as the agent reads it. Git allows newlines and control
    characters in names; quoted, a planted name cannot start a line of its own."""
    return path if path.isprintable() else json.dumps(path)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _target(payload: dict) -> tuple[Path, str] | None:
    """(repo root, repo-relative path), or None when this call is not ours."""
    if payload.get("tool_name") not in EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw or not raw.endswith(".py"):
        return None
    cwd = payload.get("cwd")
    cwd_path = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    abs_path = Path(raw) if Path(raw).is_absolute() else cwd_path / raw
    # A new file's directory may not exist yet; the checkout still answers.
    where = abs_path.parent if abs_path.parent.is_dir() else cwd_path
    try:
        repo = local.root(str(where))
        return repo, local.seeds([str(abs_path)], repo)[0]
    except (XrayError, ValueError, OSError):
        return None


def _claim_path(state_dir: Path, session_id: str, root: Path, rel: str) -> Path:
    """One file per (session, repo, file): its existence is "already shown".

    A file per claim rather than one list per session, because hooks run in
    parallel: two read-modify-writes of a shared list lose one record, and the
    lost file is denied again on its retry."""
    from ..subagent_hook import safe_session

    name = hashlib.sha256(f"{root}\0{rel}".encode()).hexdigest()[:32]
    return state_dir / f"xray-shown-{safe_session(session_id)}" / name


def _claim(path: Path) -> bool:
    """Create the claim atomically. False if another hook got there first, or
    if it cannot be written - a deny that is not recorded would never lift."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        os.close(os.open(path, flags, 0o600))
        return True
    except OSError:
        return False


def _graph(root: Path, state_dir: Path):
    """(files, links, ranks) for the checkout: cached by fingerprint."""
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    cache = state_dir / f"xray-graph-{digest}.json"
    fp = extract.fingerprint(root)
    data = _read_json(cache)
    try:
        if isinstance(data, dict) and data["fingerprint"] == fp:
            files = {str(k): bool(v) for k, v in data["files"].items()}
            links = [rank.Link(s, d, bool(c), bool(i)) for s, d, c, i in data["links"]]
            ranks = {str(k): float(v) for k, v in data["ranks"].items()}
            return files, links, ranks
    except (KeyError, TypeError, ValueError, AttributeError):
        pass  # a corrupt cache is rebuilt, not fatal
    ex = extract.extract(root)
    files = {f.path: f.is_test for f in ex.files}
    links = [rank.Link(e.src, e.dst, e.certain, e.implicit) for e in ex.edges]
    ranks = rank.pagerank(sorted(files), links)
    _write_json(
        cache,
        {
            "fingerprint": ex.fingerprint,
            "files": files,
            "links": [[ln.src, ln.dst, ln.certain, ln.implicit] for ln in links],
            "ranks": ranks,
        },
    )
    return files, links, ranks


def _render(rel: str, blast: dict) -> str:
    code, tests = blast["code"], blast["tests"]
    code_total, tests_total = blast["code_total"], blast["tests_total"]
    n_files = code_total + tests_total
    noun = "candidate test" if tests_total == 1 else "candidate tests"
    lines = [
        f"GINGUGU X-RAY: {_show(rel)} is imported by {n_files} file{'' if n_files == 1 else 's'} "
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
        claim = _claim_path(state_dir, session_id, root, rel)
        if claim.exists():
            return None
        files, links, ranks = _graph(root, state_dir)
        if rel not in files:
            return None
        blast = _without_seed(rank.blast([rel], files, links, ranks), rel)
        if blast["code_total"] + blast["tests_total"] == 0:
            return None
        # Deny only once the claim is on disk, and only for the hook that made it.
        if not _claim(claim):
            return None
        _sweep(state_dir)
        return _render(rel, blast)
    except (XrayError, ValueError, OSError):
        return None
