"""X23: links as structured rows and as compact text for an agent.

Every name here came out of someone's checkout: paths and repo names are made
JSON-safe (``_safe``) in rows and quoted (``_show``) in text, and the text is cut
at whole lines to fit an agent's context budget.
"""

from __future__ import annotations

from ..brief import _safe
from ..hook_common import _show
from .join import Shared
from .model import Link
from .ripple import Reach

MAX_CHARS = 8800
MAX_LINES = 12  # per list in the text; the rows carry the rest
MAX_DETAIL = 60


def row(lk: Link, drift: str | None = None) -> dict:
    flags = list(lk.flags) + ([drift] if drift else [])
    return {
        "from": _safe(lk.src),
        "file": _safe(lk.src_path),
        "line": lk.src_line,
        "to": _safe(lk.dst),
        "target": _safe(lk.dst_path),
        "kind": lk.kind,
        "name": _safe(lk.key),
        "detail": _safe(lk.detail),
        "certain": lk.certain,
        "flags": flags,
    }


def shared_row(s: Shared) -> dict:
    return {
        "host": _safe(s.key),
        "repos": [_safe(r) for r in s.repos],
        "evidence": [{"repo": _safe(r), "file": _safe(p), "line": n} for r, p, n in s.evidence],
    }


def reach_row(r: Reach) -> dict:
    return {
        "repo": _safe(r.repo),
        "hop": r.hop,
        "certain": r.certain,
        "via": [row(lk) for lk in r.via],
    }


def _cut(s: str) -> str:
    return s if len(s) <= MAX_DETAIL else s[:MAX_DETAIL] + "..."


def _what(r: dict) -> str:
    what = f"{r['kind']} {_show(_cut(r['name']))}"
    if r["detail"] and r["kind"] in ("ci", "pin", "cite"):
        detail = r["detail"][:12] if r["kind"] == "cite" else _cut(r["detail"])
        what += f"@{_show(detail)}"
    if r["flags"]:
        what += f", {', '.join(r['flags'])}"
    return what


def _line_in(r: dict) -> str:
    mark = "" if r["certain"] else "? "
    target = _show(r["target"]) if r["target"] else "(whole repo)"
    return f"- {mark}{_show(r['from'])} {_show(r['file'])}:{r['line']} -> {target} [{_what(r)}]"


def _line_out(r: dict) -> str:
    mark = "" if r["certain"] else "? "
    target = f":{_show(r['target'])}" if r["target"] else ""
    return f"- {mark}{_show(r['file'])}:{r['line']} -> {_show(r['to'])}{target} [{_what(r)}]"


def _list(title: str, rows: list[dict], total: int, fmt) -> list[str]:
    if not rows:
        return []
    sure = sum(1 for r in rows if r["certain"])
    out = [f"{title} ({total} link{'' if total == 1 else 's'}, {sure} certain):"]
    out += [fmt(r) for r in rows[:MAX_LINES]]
    if total > min(len(rows), MAX_LINES):
        out.append(f"(+{total - min(len(rows), MAX_LINES)} more; code_xray links has them all)")
    return out


def ripple_lines(reach: list[dict]) -> list[str]:
    if not reach:
        return []
    out = ["Ripple across repos:"]
    for hop in sorted({r["hop"] for r in reach}):
        names = [
            _show(r["repo"]) + ("" if r["certain"] else " (uncertain)")
            for r in reach
            if r["hop"] == hop
        ]
        out.append(f"- hop {hop}: {', '.join(names)}")
    return out


def read_line(read: dict) -> str:
    line = f"{read['repos']} checkouts read"
    if read["partial"]:
        line += f", {len(read['partial'])} partly (time budget)"
    if read["unscanned"]:
        line += f", {len(read['unscanned'])} not read"
    return line


def _fit(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CHARS)
    return text[: cut if cut > 0 else MAX_CHARS]


def section(data: dict) -> list[str]:
    """The lines shared by the links answer and the brief."""
    what = "these files" if "files" in data else "this repo"
    lines = _list(
        f"Other repos leaning on {what}",
        data["leaning_on_this"],
        data["leaning_on_this_total"],
        _line_in,
    )
    lines += ripple_lines(data.get("ripple", []))
    return lines


def text(data: dict) -> str:
    head = f"GINGUGU X-RAY LINKS: {_show(data['repo'])} - {read_line(data['read'])}"
    lines = [head] + section(data)
    if "this_leans_on" in data:
        lines += _list(
            "This repo leans on", data["this_leans_on"], data["this_leans_on_total"], _line_out
        )
    if data["shared"]:
        lines.append("Private hosts it shares with other repos, served by none read here:")
        lines += [
            f"- {_show(s['host'])}: {', '.join(map(_show, s['repos']))}" for s in data["shared"]
        ]
    if len(lines) == 1:
        lines.append("No links to or from the other checkouts read.")
    return _fit(lines)
