"""X11, lean: X-ray scores its own test predictions against the tests that fail.

The post-edit note records two predictions per edited file - the tests nearest
the edited definitions by call (``calls``) and the tests nearest the file by
import (``imports``). A ``pytest`` run in the same session records which test
files failed. ``score`` then says, per predictor, how many failed test files
were in its first five, further down its list, or missing - whether X-ray helps
is measured, and the two predictors can be compared on real work.

Local only: ``xray-ledger.jsonl`` sits beside the hook state, owner-only, with
repo-relative paths and hashed session and repo ids. It never reaches the brain.
Like every hook it must never break a session: recording never raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import local
from .hook_common import _show

MAX_BYTES = 5_000_000
KINDS = ("calls", "imports")
TOP = 5
MAX_TESTS = 200
MAX_MISSES = 10
# pytest as a command: at the start, after a separator, after `run` (uv run) or
# `-m` (python -m), past env assignments. `grep pytest` and `cat pytest.ini` are
# arguments, not commands.
_RUN = re.compile(
    r"(?:^|&&|\|\||[;|]|\brun|\s-m)\s*(?:\w+=\S*\s+)*(?:\S*/)?(?:pytest|py\.test)(?=\s|$)"
)
# Only a test file's path is kept from output: a log line that happens to start
# with ERROR must not put its next word (a host, a token) on disk for good.
_FAILED = re.compile(r"^(?:FAILED|ERROR) ([\w./\\-]+?\.py)(?:::|\s|$)", re.MULTILINE)


def path(state_dir: Path) -> Path:
    return state_dir.parent / "xray-ledger.jsonl"


def _hash(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _append(state_dir: Path, record: dict) -> None:
    """One JSON line, owner-only; the file moves aside to ``.1`` past the cap."""
    target = path(state_dir)
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            if target.stat().st_size > MAX_BYTES:
                os.replace(target, target.with_name(target.name + ".1"))
        except FileNotFoundError:
            pass
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def predict(
    state_dir: Path, session_id: str, root: Path, rel: str, kind: str, tests: list[str]
) -> None:
    """Record the tests X-ray named for an edit of ``rel``."""
    _append(
        state_dir,
        {
            "t": "p",
            "s": _hash(session_id),
            "r": _hash(root),
            "file": rel,
            "kind": kind,
            "tests": tests[:MAX_TESTS],
            "ts": time.time(),
        },
    )


def is_test_run(command: str) -> bool:
    return bool(_RUN.search(command))


def failed_tests(text: str) -> list[str]:
    """Test files named by pytest's ``FAILED``/``ERROR`` summary lines."""
    return sorted({m.group(1).replace("\\", "/") for m in _FAILED.finditer(text)})


def observe(payload: dict, state_dir: Path) -> None:
    """Record the outcome of a finished ``pytest`` run (Bash PostToolUse)."""
    try:
        from .edit_hook import enabled

        if payload.get("tool_name") != "Bash" or not enabled():
            return
        tool_input, response = payload.get("tool_input"), payload.get("tool_response")
        session_id = payload.get("session_id")
        if not isinstance(tool_input, dict) or not isinstance(response, dict):
            return
        command = tool_input.get("command")
        if not isinstance(command, str) or not is_test_run(command):
            return
        if response.get("interrupted") or not isinstance(session_id, str) or not session_id:
            return
        text = "\n".join(
            v for v in (response.get("stdout"), response.get("stderr")) if isinstance(v, str)
        )
        cwd = payload.get("cwd")
        root = local.root(cwd if isinstance(cwd, str) and cwd else os.getcwd())
        _append(
            state_dir,
            {
                "t": "o",
                "s": _hash(session_id),
                "r": _hash(root),
                "failed": failed_tests(text)[:MAX_TESTS],
                "ts": time.time(),
            },
        )
    except Exception:  # noqa: BLE001 - a bug here never breaks a command
        pass


def _records(state_dir: Path) -> list[dict]:
    try:
        lines = path(state_dir).read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(row, dict):
            continue
        # The ledger is a file anyone with the owner's access can edit: a row of the
        # wrong shape is skipped, never trusted into a KeyError.
        if row.get("t") == "p" and isinstance(row.get("file"), str):
            tests = row.get("tests")
            if isinstance(tests, list) and row.get("kind") in KINDS:
                out.append({**row, "tests": [t for t in tests if isinstance(t, str)]})
        elif row.get("t") == "o" and isinstance(row.get("failed"), list):
            out.append({**row, "failed": [t for t in row["failed"] if isinstance(t, str)]})
    return out


def _bucket(test: str, preds: list[list[str]]) -> str:
    """top5 / listed / missed for one failed test against one predictor's lists."""
    if any(test in tests[:TOP] for tests in preds):
        return "top5"
    return "listed" if any(test in tests for tests in preds) else "missed"


def score(state_dir: Path) -> dict:
    """How the predictions fared against the recorded test runs."""
    out: dict = {
        "runs": 0,
        "failing_runs": 0,
        "failed": 0,
        "by_kind": {k: {"top5": 0, "listed": 0, "missed": 0} for k in KINDS},
        "unpredicted": 0,
        "misses": [],
    }
    seen: dict[tuple, list[dict]] = {}
    for row in _records(state_dir):
        key = (row.get("s"), row.get("r"))
        if row["t"] == "p":
            seen.setdefault(key, []).append(row)
            continue
        failed = row["failed"]
        out["runs"] += 1
        out["failing_runs"] += bool(failed)
        out["failed"] += len(failed)
        preds = seen.get(key, [])
        for test in failed:
            if not preds:
                out["unpredicted"] += 1
                continue
            hits = []
            for kind in KINDS:
                lists = [p["tests"] for p in preds if p.get("kind") == kind]
                hit = _bucket(test, lists) if lists else "missed"
                out["by_kind"][kind][hit] += 1
                if lists:
                    hits.append(hit)
            if all(h == "missed" for h in hits):
                edited = sorted({p["file"] for p in preds})
                out["misses"].append({"test": test, "edited": edited})
    out["misses"] = out["misses"][-MAX_MISSES:]
    return out


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def render(result: dict) -> str:
    """The score as a few short lines."""
    lines = [
        f"X-RAY SCORE: {_plural(result['runs'], 'test run')}, {result['failing_runs']} failing "
        f"({_plural(result['failed'], 'failed test file')})."
    ]
    parts = []
    for kind in KINDS:
        b = result["by_kind"][kind]
        parts.append(
            f"{kind}: {b['top5']} in top 5, {b['listed']} further down, {b['missed']} missed"
        )
    lines.append("; ".join(parts))
    if result["unpredicted"]:
        lines.append(f"Unpredicted (no edit note in that session): {result['unpredicted']}")
    for miss in result["misses"][-5:]:
        # Both came from disk and stdout: quoted if unprintable, like every X-ray path.
        edited = ", ".join(_show(p) for p in miss["edited"])
        lines.append(f"Missed: {_show(miss['test'])} (after editing {edited})")
    return "\n".join(lines)
