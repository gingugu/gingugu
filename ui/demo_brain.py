"""Build the Explorer's public demo brain, then dump it as the static demo.

A fictional brain big enough to show the 3D view at scale and varied enough to
exercise every field the Explorer draws: all memory types, confidences and
relation types, pins, provenance and ``about``, capabilities, claims,
tripwires, recall history, a query log, dormant memories, date tags, and
markdown bodies with ``[[wiki links]]``. Every name comes from
``demo_corpus.py`` - never from a real brain. Deterministic for a given seed.

    uv run python ui/demo_brain.py              # regenerate ui/src/data/static
    uv run python ui/demo_brain.py --db x.db    # also keep the demo DB
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_corpus import (  # noqa: E402 - sibling module, path set above
    ABOUTS,
    ALTERNATIVES,
    CONDITIONS,
    CORE,
    FAILURES,
    HUB_TAGS,
    NAMESPACES,
    PERSONA,
    REASONS,
    THINGS,
    TOOLS,
    VERBS,
)  # fmt: skip

from gingugu.database import Database  # noqa: E402
from gingugu.portability import import_data  # noqa: E402

NOW = datetime.fromisoformat("2026-10-20T12:00:00+00:00")
NOW_ISO = NOW.isoformat()
SPAN_DAYS = 180
TYPES = ["fact", "decision", "pattern", "bug", "architecture", "preference", "workflow",
         "context", "capability"]  # fmt: skip
CONFIDENCE = ["verified"] * 15 + ["inferred"] * 3 + ["stale", "deprecated"]
PROVENANCE = ["user-asserted", "measured", "file-derived", "self-concluded", None]
RELATIONS = ["related_to", "caused_by", "child_of", "parent_of", "contradicts"]


class Gen:
    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)

    def uid(self) -> str:
        return str(uuid.UUID(int=self.r.getrandbits(128), version=4))

    def when(self, lo: datetime, hi: datetime = NOW) -> datetime:
        span = max(1.0, (hi - lo).total_seconds())
        return lo + timedelta(seconds=int(self.r.random() * span))

    def pick(self, seq):
        return self.r.choice(seq)

    def title(self, kind: str, topic: str) -> str:
        p, t = self.pick, topic.replace("-", " ")
        a, b = p(ALTERNATIVES)
        return {
            "decision": f"Use {a} over {b} for the {t} {p(THINGS)}",
            "bug": f"The {t} {p(THINGS)} {p(FAILURES)} when {p(CONDITIONS)}",
            "pattern": f"Pattern: {p(VERBS)} the {t} {p(THINGS)} before it {p(FAILURES)}",
            "preference": f"RULE: never {p(VERBS)} {t} by hand",
            "workflow": f"How to {p(VERBS)} the {t} {p(THINGS)}",
            "architecture": f"The {t} {p(THINGS)} sits behind the {p(THINGS)}",
            "fact": f"The {t} {p(THINGS)} {p(['caps at', 'retries', 'buffers'])} "
            f"{self.r.randint(2, 500)} items",
            "context": f"Notes from the {t} {p(['spike', 'review', 'outage', 'demo'])}",
            "capability": f"CAPABILITY: {p(VERBS)} the {t} {p(THINGS)} with {p(TOOLS)}",
        }[kind]


def _unique(gen: Gen, kind: str, topic: str, seen: set[str]) -> str:
    for _ in range(20):
        title = gen.title(kind, topic)
        if title.lower() not in seen:
            seen.add(title.lower())
            return title
    title = f"{gen.title(kind, topic)} ({len(seen)})"
    seen.add(title.lower())
    return title


def _body(gen: Gen, mem: dict, topic: str, near: list[dict], core: list[dict]) -> str:
    r, p = gen.r, gen.pick
    lines = [f"{mem['title']}. Settled after {p(['a long spike', 'two bad runs', 'review'])}.",
             "", "## Why", f"- {p(REASONS)}", f"- {p(REASONS)}", ""]  # fmt: skip
    if r.random() < 0.3:
        lines += ["```sh", f"{p(TOOLS).split()[-1]} {p(VERBS).replace(' ', '-')} {topic}",
                  "```", ""]  # fmt: skip
    if r.random() < 0.15:
        lines += ["| setting | value |", "| --- | --- |",
                  f"| {topic} | {r.randint(1, 90)} |", ""]  # fmt: skip
    links = [p(near)["title"] for _ in range(r.randint(1, 2))] + [p(core)["title"]]
    lines.append("See " + " and ".join(f"[[{t}]]" for t in links) + ".")
    if r.random() < 0.12:
        lines.append(f"Spec: [the {topic} notes](https://example.com/{topic}).")
    if r.random() < 0.05:
        lines.append(f"![{topic} diagram](https://example.org/{topic}.png)")
    return "\n".join(lines)


def _memories(gen: Gen) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    seen: set[str] = set()
    namespaces, by_ns = [], {}
    for name, (count, topics) in NAMESPACES.items():
        ns_id = gen.uid()
        namespaces.append({"id": ns_id, "name": name, "path": None, "description": None,
                           "created_at": (NOW - timedelta(days=SPAN_DAYS)).isoformat(),
                           "updated_at": NOW_ISO})  # fmt: skip
        by_ns[name] = []
        for i in range(count):
            kind = TYPES[i % len(TYPES)] if i < len(TYPES) else gen.pick(TYPES)
            topic = topics[min(int(gen.r.paretovariate(1.1)) - 1, len(topics) - 1)]
            created = gen.when(NOW - timedelta(days=SPAN_DAYS))
            tags = {topic, gen.pick(topics)}
            if gen.r.random() < 0.3:
                tags.add(gen.pick(HUB_TAGS))
            if gen.r.random() < 0.35:
                tags.add(created.date().isoformat())
            conf = "verified" if i < 3 else gen.pick(CONFIDENCE)
            mem = {
                "id": gen.uid(), "namespace_id": ns_id, "type": kind,
                "title": _unique(gen, kind, topic, seen), "content": "", "confidence": conf,
                "source": None, "created_at": created.isoformat(),
                "updated_at": created.isoformat(), "last_accessed": created.isoformat(),
                "last_confirmed": created.isoformat(), "access_count": 0,
                "metadata": None, "pinned": 0, "provenance": gen.pick(PROVENANCE),
                "about": None, "tags": sorted(tags), "_topic": topic,
            }  # fmt: skip
            if gen.r.random() < 0.4:
                mem["about"] = gen.pick(ABOUTS).format(
                    topic=topic.replace("-", " "), thing=gen.pick(THINGS), verb=gen.pick(VERBS)
                )
            if kind == "capability":
                slug = topic.replace(" ", "-")
                mem["metadata"] = json.dumps(
                    {"capability": {"run": f"make {slug}", "path": f"scripts/{slug}.sh"}}
                )
            if kind in ("preference", "architecture") and gen.r.random() < 0.06:
                mem["pinned"] = 1
            by_ns[name].append(mem)
    memories = [m for ms in by_ns.values() for m in ms]
    for ms in by_ns.values():
        for m in ms:
            m["content"] = _body(gen, m, m.pop("_topic"), ms, by_ns[CORE])
    return namespaces, memories, by_ns


def _relations(gen: Gen, by_ns: dict[str, list[dict]]) -> list[dict]:
    rels, keys = [], set()

    def add(src: dict, dst: dict, kind: str) -> None:
        if src["id"] != dst["id"] and (src["id"], dst["id"], kind) not in keys:
            keys.add((src["id"], dst["id"], kind))
            rels.append({"id": gen.uid(), "source_id": src["id"], "target_id": dst["id"],
                         "relation_type": kind, "created_at": src["created_at"],
                         "metadata": None})  # fmt: skip

    for ms in by_ns.values():
        live = [m for m in ms if m["confidence"] == "verified"]
        for m in ms:
            if m["confidence"] == "deprecated" and live:
                add(gen.pick(live), m, "supersedes")
            for _ in range(gen.pick([0, 1, 1, 1, 2])):
                pool = by_ns[CORE] if gen.r.random() < 0.15 else ms
                add(m, gen.pick(pool), gen.pick(RELATIONS))
    return rels


def build(db_path: Path, seed: int = 7) -> dict:
    """Write the demo brain to ``db_path`` (which must not exist yet)."""
    from demo_activity import add_activity  # noqa: PLC0415 - sibling, path set above

    if Path(db_path).exists():
        raise SystemExit(f"refusing to overwrite {db_path}")
    gen = Gen(seed)
    namespaces, memories, by_ns = _memories(gen)
    relations = _relations(gen, by_ns)
    conn = Database(Path(db_path)).connect()
    import_data(conn, {"format_version": 1, "exported_at": NOW_ISO, "namespaces": namespaces,
                       "memories": memories, "relations": relations})  # fmt: skip
    add_activity(conn, gen, memories, by_ns, NOW)
    conn.commit()
    conn.close()
    return {"memories": len(memories), "relations": len(relations)}


def main() -> int:
    from dump_static import DEFAULT_OUT, write  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Regenerate the Explorer's static demo.")
    parser.add_argument("--db", type=Path, help="Also keep the demo DB at this new path.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        db = args.db or Path(tmp) / "demo.db"
        print(build(db), file=sys.stderr)
        write(db, args.out, persona=PERSONA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
