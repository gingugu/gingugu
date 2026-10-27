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
from pathlib import Path

from . import extract, local, rank
from .scan import XrayError

TOP_N = 5
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_TRIPWIRE", "on").lower() not in ("0", "off", "false")


def _write_json(path: Path, data: object) -> None:
    """Owner-only, no symlink following; the files are optimisations, so a
    failed write is swallowed (same pattern as ``hook_remote_flows``)."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data))
        os.chmod(path, 0o600)  # an older, looser file keeps its mode on O_CREAT
    except OSError:
        pass


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


def _tripped_path(state_dir: Path, session_id: object) -> Path:
    from ..subagent_hook import safe_session

    sid = safe_session(session_id if isinstance(session_id, str) else "")
    return state_dir / f"xray-tripped-{sid}.json"


def _load_tripped(path: Path) -> list[str]:
    data = _read_json(path)
    return [k for k in data if isinstance(k, str)] if isinstance(data, list) else []


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
        f"GINGUGU X-RAY: {rel} is imported by {n_files} file{'' if n_files == 1 else 's'} "
        f"({tests_total} {noun})."
    ]
    if code:
        lines.append("Top dependents by rank:")
        for row in code[:TOP_N]:
            hop = row["hops"]
            lines.append(f"- {row['path']} ({hop} hop{'' if hop == 1 else 's'})")
        if code_total > TOP_N:
            lines.append(f"... and {code_total - TOP_N} more")
    if tests:
        listed = ", ".join(row["path"] for row in tests[:TOP_N])
        more = f" (+{tests_total - TOP_N} more)" if tests_total > TOP_N else ""
        lines.append(f"Candidate tests: {listed}{more}")
    lines.append(
        "Import-based estimate: strings, fixtures and subprocesses are not edges. "
        "Check the callers, then re-issue the edit unchanged - the X-ray fires "
        "once per file per session."
    )
    return "\n".join(lines)


def check(payload: dict, state_dir: Path) -> str | None:
    """A deny reason for the first edit of an imported file, else None."""
    try:
        target = _target(payload)
        if target is None:
            return None
        root, rel = target
        tripped_file = _tripped_path(state_dir, payload.get("session_id"))
        key = f"{root}\0{rel}"
        tripped = _load_tripped(tripped_file)
        if key in tripped:
            return None
        files, links, ranks = _graph(root, state_dir)
        if rel not in files:
            return None
        blast = rank.blast([rel], files, links, ranks)
        if blast["code_total"] + blast["tests_total"] == 0:
            return None
        _write_json(tripped_file, [*tripped, key])
        return _render(rel, blast)
    except (XrayError, ValueError, OSError):
        return None
