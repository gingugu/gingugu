"""X19: the X-ray brief - what a change touches and what it reaches.

For an agent that never fires the read or edit hooks (it reads through a shell
and edits with a patch tool): the changed files by rank and the combined blast
of the changed source, from the same cached graph the hooks use. Computed on
this machine; nothing is stored and the brain is never asked.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from . import docs_map, hook_common, local, rank, scan
from .hook_common import _show
from .hook_common import importers as _importers
from .read_hook import _ranked

# Claude Code caps additionalContext at 10,000 characters; same margin as read_hook.
MAX_CHARS = 8800
TOP = 5
CAVEAT = (
    "Import-based estimate: strings, fixtures, subprocesses, config and data "
    "dependencies are not edges. Tests are candidates to run; unreachable files are "
    "candidates to inspect, not proven dead."
)
_STATUSES = ("A", "M", "D", "R")
_WORDS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
# Past this the blast lists alone could crowd the changed files out of the text.
MAX_LIMIT = 50
MAX_DOCS_SHOWN = 5
# Files per docs section: the changed-file list must not be crowded out of the text.
MAX_DOC_ROWS = 10
# The range may be another agent's choice, and a brief can run inside a hook.
GIT_TIMEOUT_S = 10


def _safe(path: str | None) -> str | None:
    """A non-UTF-8 name decodes to lone surrogates, which no JSON encoder accepts."""
    if path is None:
        return None
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


class Change(NamedTuple):
    status: str  # A, M, D or R
    path: str
    old_path: str | None


def _check_range(rev_range: str) -> None:
    """Refused before git sees it: an option or smuggled text, not a revision."""
    if not rev_range or rev_range.startswith("-"):
        raise ValueError("range must be a git revision range, e.g. main..HEAD")
    if any(c.isspace() or not c.isprintable() for c in rev_range):
        raise ValueError("range must not contain whitespace or control characters")


def _tokens(out: bytes) -> list[str]:
    return [t.decode("utf-8", "surrogateescape") for t in out.split(b"\0") if t]


def _parse(out: bytes) -> list[Change]:
    """``git diff --name-status -z``: status, path - or status, old, new for R/C."""
    toks, i, found = _tokens(out), 0, []
    while i < len(toks):
        code = toks[i][:1]
        if code in ("R", "C") and i + 2 < len(toks):
            old, new = toks[i + 1], toks[i + 2]
            found.append(Change("R", new, old) if code == "R" else Change("A", new, None))
            i += 3
        elif i + 1 < len(toks):
            found.append(Change(code if code in ("A", "M", "D") else "M", toks[i + 1], None))
            i += 2
        else:
            break
    return found


def _run(repo: Path, *args: str) -> bytes:
    proc = scan.git(repo, *args, timeout=GIT_TIMEOUT_S)
    if proc.returncode != 0:
        raise scan.XrayError(proc.stderr.decode(errors="replace").strip() or "git failed")
    return proc.stdout


def changes(
    repo: Path, rev_range: str | None = None, files: list[str] | None = None
) -> list[Change]:
    """The change set, sorted by path: a revision range, named files, or (neither)
    the working tree against HEAD with staged and untracked files."""
    if rev_range is not None and files:
        raise ValueError("pass a range or files, not both")
    if rev_range is not None:
        _check_range(rev_range)
        found = _parse(
            _run(repo, "diff", "--name-status", "-z", "-M", "--no-color", rev_range, "--")
        )
    elif files:
        found = [Change("M" if (repo / f).exists() else "D", f, None) for f in files]
    elif scan.commit(repo) is None:
        listed = _run(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        found = [Change("A", p, None) for p in _tokens(listed)]
    else:
        found = _parse(_run(repo, "diff", "--name-status", "-z", "-M", "--no-color", "HEAD", "--"))
        seen = {c.path for c in found}
        untracked = _tokens(_run(repo, "ls-files", "--others", "--exclude-standard", "-z"))
        found += [Change("A", p, None) for p in untracked if p not in seen]
    return sorted(found, key=lambda c: c.path)


def _docs(docs: docs_map.DocsMap | None, changes: list[Change]) -> tuple[list, list]:
    """(docs citing each added or changed file, docs still citing a deleted or
    renamed-away one): both empty without a map."""
    if docs is None:
        return [], []

    def rows(found):
        return [{"doc": _safe(c.doc), "repo": _safe(c.repo)} for c in found]

    live, gone = [], []
    for c in changes:
        if c.status in ("A", "M", "R"):
            found = docs_map.citing(docs, c.path)
            if found:
                live.append({"path": _safe(c.path), "docs": rows(found)})
        gone_path = c.path if c.status == "D" else c.old_path if c.status == "R" else None
        if gone_path is not None:
            found = docs_map.citing_gone(docs, gone_path)
            if found:
                gone.append({"path": _safe(gone_path), "status": c.status, "docs": rows(found)})
    return live, sorted(gone, key=lambda r: r["path"])


def build(
    g: hook_common.Graph,
    changes: list[Change],
    limit: int = 10,
    docs: docs_map.DocsMap | None = None,
) -> dict:
    """Pure: the changed files placed in the graph, and what the source reaches."""
    source = _ranked(g)
    position = {p: n for n, p in enumerate(source, 1)}
    imported_by = _importers(g)
    rows = [
        {
            "path": _safe(c.path),
            "status": c.status,
            "old_path": _safe(c.old_path),
            "source": c.path in position and c.status != "D",
            "rank": position.get(c.path),
            "importers": len(imported_by.get(c.path, ())),
        }
        for c in changes
    ]
    rows.sort(key=lambda r: (not r["source"], r["rank"] or 0, r["path"]))
    seeds = [c.path for c in changes if c.path in position and c.status != "D"]
    reach = rank.blast(seeds, g.files, g.links, g.ranks, limit=limit, hidden=g.packages)
    live, gone = _docs(docs, changes)
    return {
        "changed": rows,
        "counts": {s: sum(1 for c in changes if c.status == s) for s in _STATUSES},
        "other": sum(1 for r in rows if not r["source"] and r["status"] != "D"),
        "blast": {
            "code": [_safe(r["path"]) for r in reach["code"]],
            "tests": [_safe(r["path"]) for r in reach["tests"]],
            "code_total": reach["code_total"],
            "tests_total": reach["tests_total"],
        },
        "files": len(source),
        "top": [_safe(p) for p in source[:TOP]],
        "docs": live,
        "docs_gone": gone,
    }


def _doc_lines(title: str, rows: list[dict], word: bool = False) -> list[str]:
    """A line per file: its first ``MAX_DOCS_SHOWN`` docs, then a count of the rest."""
    if not rows:
        return []
    out = [title]
    for r in rows[:MAX_DOC_ROWS]:
        shown = [
            _show(d["doc"] if d["repo"] is None else f"{d['repo']}:{d['doc']}") for d in r["docs"]
        ]
        more = f" (+{len(shown) - MAX_DOCS_SHOWN} more)" if len(shown) > MAX_DOCS_SHOWN else ""
        label = _show(r["path"]) + (f" ({_WORDS[r['status']]})" if word else "")
        out.append(f"- {label}: {', '.join(shown[:MAX_DOCS_SHOWN])}{more}")
    if len(rows) > MAX_DOC_ROWS:
        out.append(f"(+{len(rows) - MAX_DOC_ROWS} more files; code_xray docs names them all)")
    return out


def _line(r: dict) -> str:
    path = _show(r["path"])
    if r["old_path"]:
        path = f"{_show(r['old_path'])} -> {path}"
    if r["source"]:
        n = r["importers"]
        return f"- {r['status']} {path} (rank {r['rank']}, {n} importer{'' if n == 1 else 's'})"
    return f"- {r['status']} {path}"


def render(data: dict, name: str) -> str:
    """Compact text for an agent. Paths are quoted by ``_show``; when it will not
    fit, the changed-file list gives way (with a count), never a line mid-way."""
    counts = data["counts"]
    total = sum(counts.values())
    summary = ", ".join(f"{counts[s]} {_WORDS[s]}" for s in _STATUSES if counts[s])
    head = f"GINGUGU X-RAY BRIEF: {_show(name)} - " + (
        f"{total} changed ({summary})" if total else "no changes"
    )
    if data["other"]:
        head += f"; {data['other']} not source"
    blast = data["blast"]
    tail = []
    if any(r["source"] for r in data["changed"]):
        reach = blast["code_total"] + blast["tests_total"]
        tail.append(f"Reaches {reach} files ({blast['tests_total']} candidate tests):")
        tail += [f"- {_show(p)}" for p in blast["code"] + blast["tests"]]
        if len(blast["code"]) < blast["code_total"] or len(blast["tests"]) < blast["tests_total"]:
            tail.append("(lists are paged; code_xray blast has the rest)")
    tail += _doc_lines("Docs citing the changed files:", data.get("docs", []))
    tail += _doc_lines(
        "Docs still citing a deleted or renamed file:", data.get("docs_gone", []), True
    )
    if data["top"]:
        top = ", ".join(map(_show, data["top"]))
        tail.append(f"Repo top files ({data['files']} source): {top}")
    tail_text = "\n".join(tail)
    budget = MAX_CHARS - len(head) - len(tail_text) - 80  # newlines and the "+N more" line
    lines, used = [], 0
    for r in data["changed"]:
        line = _line(r)
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) < len(data["changed"]):
        lines.append(f"(+{len(data['changed']) - len(lines)} more)")
    text = "\n".join([head, *lines, *([tail_text] if tail_text else [])])
    return text[:MAX_CHARS]


def _docs_map(repo: Path, state: Path) -> docs_map.DocsMap | None:
    """The docs map, or None when it is off or fails: docs never cost a brief."""
    if not docs_map.enabled():
        return None
    try:
        return docs_map.read(repo, state)
    except Exception:  # noqa: BLE001
        return None


def answer(
    path: str | None,
    rev_range: str | None,
    files,
    limit: int = MAX_LIMIT,
    *,
    session_id: str | None = None,
) -> dict:
    """The whole brief for one checkout; shared by the handler, the proxy, the CLI and
    the board. With a session, a graph build that was killed is not retried in it."""
    from ..config import load_config

    repo = local.root(path)
    named = local.file_list(files)
    if rev_range is not None and named:
        raise ValueError("pass a range or files, not both")
    rel = [p for p in local.seeds(named, repo) if p] if named else None  # "." is no file
    found = changes(repo, rev_range, rel)  # a ref git rejects fails before any build
    state = load_config().db_path.parent / "hook-sessions"
    g = hook_common.graph(repo, state, session_id)
    data = build(g, found, limit=min(max(0, limit), MAX_LIMIT), docs=_docs_map(repo, state))
    return {"ok": True, **data, "text": render(data, repo.name), "caveat": CAVEAT}
