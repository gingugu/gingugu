"""The Explorer's public demo brain (``ui/demo_brain.py``).

The demo ships in the wheel and on GitHub Pages, so it is held to two rules:
every name and word in it is invented (nothing may come from a real brain),
and it is big and varied enough to exercise every field the Explorer draws.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

from gingugu.models import Confidence, MemoryType, Provenance, RelationType

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ui" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load("demo_brain")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    demo.build(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    yield path, conn
    conn.close()


def _col(conn, sql: str) -> list:
    return [r[0] for r in conn.execute(sql)]


def test_namespaces_are_exactly_the_invented_set(built):
    _, conn = built
    names = set(_col(conn, "SELECT name FROM namespaces"))
    assert names == set(demo.NAMESPACES)
    assert demo.CORE in names and demo.PERSONA in names


def test_big_enough_to_show_the_ui_at_scale(built):
    _, conn = built
    assert _col(conn, "SELECT COUNT(*) FROM memories")[0] >= 2000
    assert _col(conn, "SELECT COUNT(*) FROM relations")[0] >= 1500


def test_every_type_confidence_and_provenance_appears(built):
    _, conn = built
    assert set(_col(conn, "SELECT DISTINCT type FROM memories")) == {t.value for t in MemoryType}
    assert set(_col(conn, "SELECT DISTINCT confidence FROM memories")) == {
        c.value for c in Confidence
    }
    assert {p.value for p in Provenance} <= set(
        _col(conn, "SELECT DISTINCT provenance FROM memories WHERE provenance IS NOT NULL")
    )


def test_every_relation_type_appears(built):
    _, conn = built
    assert set(_col(conn, "SELECT DISTINCT relation_type FROM relations")) == {
        r.value for r in RelationType
    }


def test_pins_about_capabilities_claims_tripwires(built):
    _, conn = built
    assert _col(conn, "SELECT COUNT(*) FROM memories WHERE pinned = 1")[0] >= 10
    assert _col(conn, "SELECT COUNT(*) FROM memories WHERE about IS NOT NULL")[0] >= 100
    caps = _col(conn, "SELECT metadata FROM memories WHERE type = 'capability'")
    assert caps and all("capability" in json.loads(m) for m in caps)
    states = set(_col(conn, "SELECT DISTINCT state FROM memory_claims"))
    assert {"open", "resolved"} <= states
    assert _col(conn, "SELECT COUNT(*) FROM tripwires")[0] >= 10


def test_recall_history_and_query_log_for_the_synapse_views(built):
    _, conn = built
    assert _col(conn, "SELECT COUNT(*) FROM access_log")[0] >= 3000
    assert _col(conn, "SELECT COUNT(*) FROM query_log")[0] >= 300
    # access_count agrees with the log it summarizes
    mismatched = _col(
        conn,
        "SELECT COUNT(*) FROM memories m WHERE m.access_count != "
        "(SELECT COUNT(*) FROM access_log a WHERE a.memory_id = m.id)",
    )[0]
    assert mismatched == 0


def test_dormant_and_dated_memories_exist(built):
    _, conn = built
    # Dormant = untouched for 90+ days before the demo's fixed "now".
    cutoff = demo.NOW_ISO[:10]
    old = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE julianday(?) - julianday(last_accessed) >= 90",
        (cutoff,),
    ).fetchone()[0]
    assert old >= 100
    dated = _col(conn, "SELECT COUNT(*) FROM tags WHERE name GLOB '[0-9][0-9][0-9][0-9]-*'")
    assert dated[0] >= 20


def test_bodies_are_rich_markdown_with_resolving_wiki_links(built):
    _, conn = built
    titles = {t.lower() for t in _col(conn, "SELECT title FROM memories")}
    bodies = _col(conn, "SELECT content FROM memories")
    links = [m.strip().lower() for b in bodies for m in re.findall(r"\[\[([^\[\]]+)\]\]", b)]
    assert len(links) >= 500
    assert sum(link in titles for link in links) / len(links) >= 0.95
    joined = "\n".join(bodies)
    for marker in ("## ", "- ", "```", "| ", "](https://", "!["):
        assert marker in joined, marker


def test_titles_are_unique_so_links_are_unambiguous(built):
    _, conn = built
    assert _col(conn, "SELECT COUNT(*) - COUNT(DISTINCT lower(title)) FROM memories")[0] == 0


def test_deterministic(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    demo.build(a)
    demo.build(b)

    def dump(p):
        c = sqlite3.connect(p)
        sql = "SELECT id, title, content, created_at FROM memories ORDER BY id"
        rows = c.execute(sql).fetchall()
        c.close()
        return rows

    assert dump(a) == dump(b)


def test_the_committed_static_demo_is_this_demo():
    static = ROOT / "ui" / "src" / "data" / "static"
    graph = json.loads((static / "graph.json").read_text())
    info = json.loads((static / "info.json").read_text())
    assert {ns["name"] for ns in graph["namespaces"]} == set(demo.NAMESPACES)
    assert len(graph["nodes"]) >= 2000
    assert info["persona"] == demo.PERSONA
    assert info["mode"] == "static"
