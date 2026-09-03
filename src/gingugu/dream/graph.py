"""The in-memory view of the relation graph the passes compute over.

Loaded once per run and handed to every pass, because two passes disagreeing
about what the graph is would make their findings incomparable - a memory
called central by one and isolated by another, from the same run, with no way
to tell which was reading a different graph.

**Edges are undirected here.** ``relations`` stores direction, and direction is
what makes an edge worth having, but every structural question this package
asks is about connectivity rather than about which way an arrow points.
Spreading activation already treats a memory's neighbours as edges in either
direction (``relations.py``), so reading the graph the same way keeps "what is
well connected" meaning the same thing at retrieval time and at dream time.

**Scope is all-or-nothing.** With a namespace filter, an edge counts only when
*both* endpoints are inside it. Relations legitimately cross namespaces, and
counting the half-dangling ones would let a memory's rank inside ``crow`` be
inflated by how much a project namespace leans on it - answering a question
nobody asked. A store-wide run has everything in scope, so nothing is dropped.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class Graph:
    """Nodes, undirected adjacency, and the titles a report needs to be read."""

    nodes: list[str]
    adjacency: dict[str, set[str]] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    namespace_of: dict[str, str] = field(default_factory=dict)
    namespace_id_of: dict[str, str] = field(default_factory=dict)

    def degree(self, node: str) -> int:
        return len(self.adjacency.get(node, ()))

    @property
    def orphans(self) -> list[str]:
        """Nodes no edge touches, in the graph's own stable order."""
        return [n for n in self.nodes if not self.adjacency.get(n)]

    @property
    def edge_count(self) -> int:
        """Distinct undirected pairs. Each is stored once per endpoint."""
        return sum(len(v) for v in self.adjacency.values()) // 2


def load(conn: sqlite3.Connection, *, namespace_id: str | None = None) -> Graph:
    """Read the graph in scope.

    Nodes come back sorted by id. That is not cosmetic: label propagation
    visits nodes in list order and its result depends on that order, so a
    stable sort is what makes the pass reproducible. A run that returns
    different communities each time it sees the same graph is not math a person
    can audit, and auditability is the only reason this pass is allowed to run
    unattended.
    """
    where = "WHERE m.namespace_id = ?" if namespace_id else ""
    params: tuple = (namespace_id,) if namespace_id else ()

    rows = conn.execute(
        "SELECT m.id, m.title, m.namespace_id, n.name AS namespace "
        "FROM memories m JOIN namespaces n ON n.id = m.namespace_id "
        f"{where} ORDER BY m.id ASC",
        params,
    ).fetchall()

    graph = Graph(nodes=[r["id"] for r in rows])
    graph.titles = {r["id"]: r["title"] for r in rows}
    graph.namespace_of = {r["id"]: r["namespace"] for r in rows}
    graph.namespace_id_of = {r["id"]: r["namespace_id"] for r in rows}
    in_scope = set(graph.nodes)
    graph.adjacency = {node: set() for node in graph.nodes}

    for row in conn.execute("SELECT source_id, target_id FROM relations").fetchall():
        src, dst = row["source_id"], row["target_id"]
        if src == dst or src not in in_scope or dst not in in_scope:
            continue
        graph.adjacency[src].add(dst)
        graph.adjacency[dst].add(src)

    return graph
