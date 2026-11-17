"""Every version of each managed `gingugu harness` template we have shipped.

Harness files are rendered per repo (``{{namespace}}``, ``{{grant}}``), so one
shipped version has different bytes in every repo and the sha256 registry
``init`` uses for its hooks (``shipped.py``) cannot recognise it. So the older
template TEXTS are kept, under ``templates/harness/_shipped/<rel>.versions/<sha12>.tmpl``:
a file on disk is a stock copy when it equals one of them rendered for this
repo. Rendering forward is exact - including versions that spelled the grant
inline - where un-rendering the file would misfire on a repo whose name also
appears in the template's own text.

When you edit a template, ``tests/test_harness_refresh.py`` fails until you save
the outgoing text under ``_shipped/`` and pin the new hash below.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from importlib.resources import files

HARNESS_PINNED: dict[str, str] = {
    "hooks/log_event.py": "4ed12bb177600aa87a28f18fb51c6678307f17764069251af5a6814bcb165454",
    "hooks/pre_tool_use.py": "ceca92aae807ca5c9fe10f80cbcf22a824a7b7ca1fa4d775c780720491a9a3ce",
    "hooks/pre_compact.py": "5ef7db89e94f03394e02f981cde068afb1a54cd6458ae339ee5673de7773b8f9",
    "agents/repo-scout.md": "9344719b97f7237f5e3cd28c6f15ae05d6a1114c30f993e68f3ac46be394d636",
    "agents/security-reviewer.md": (
        "4728507dc7b868a1acbeea15c46445c8918a09a0d3e58134aeef9594b1b59d20"
    ),
    "agents/ai-docs-auditor.md": (
        "747cbb8dc084d03b5c524951921d5d20b23f1aa39cf1fac08fa38b3da7296407"
    ),
    "agents/researcher.md": "8aec3edc380a14301f69fca7b787e3c9f230b3ab41ed37d97cda0c1be7a3e61f",
    "skills/creating-pr/SKILL.md": (
        "cd30af565202532187a74366ac184c82120dd811b4692de65739dd6891959c9f"
    ),
    "skills/creating-pr/ai-assessment-checklist.md": (
        "4b4934e7e9d8587bc56d958d1efa8c3849f9372a63ce8b853846090e170448ce"
    ),
    "skills/creating-pr/stacking-prs.md": (
        "6343a906df4788fd088a0c4fa62fe5c5aca59539e5c823b52a4146a55383f256"
    ),
    # User-level, not rendered: the rationale file behind the delegation block.
    "docs/delegation.md": "188082f4e60b45508aabca85eac3df308db174592e37cf81119ae0c1568c1280",
}


def render(template: str, namespace: str, grant: str) -> str:
    """A harness template as it lands in a repo."""
    return template.replace("{{grant}}", grant).replace("{{namespace}}", namespace)


@cache
def previous_templates(rel: str) -> tuple[str, ...]:
    """Text of every older shipped version of ``harness/<rel>.tmpl``, LF endings."""
    node = files("gingugu.bootstrap") / "templates" / "harness" / "_shipped"
    *dirs, name = rel.split("/")
    for part in [*dirs, f"{name}.versions"]:  # never a `*.py` path in the package
        node = node / part
    if not node.is_dir():
        return ()
    return tuple(
        f.read_bytes().decode().replace("\r\n", "\n")
        for f in sorted(node.iterdir(), key=lambda f: f.name)
        if f.name.endswith(".tmpl")
    )


def stock_check(rel: str, namespace: str = "", grant: str = "") -> Callable[[bytes], bool]:
    """``is_stock`` for ``write_file``: is this file an older version, rendered here?"""
    rendered = {render(t, namespace, grant) for t in previous_templates(rel)}

    def is_stock(data: bytes) -> bool:
        try:
            text = data.decode().replace("\r\n", "\n")
        except UnicodeDecodeError:
            return False
        return text in rendered

    return is_stock
