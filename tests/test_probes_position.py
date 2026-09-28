"""Position-stratified probes (bench/probes.py).

WHY. The embedder reads only the first 512 tokens of a memory and truncates the
rest silently, and most of a real brain is longer than that. A probe set that
only ever asks about the opening of a memory cannot see that defect: measured
2026-09-27, all 126 first-unique-phrase probes sat inside the window (median
token 77), while the same targets probed from their tail dropped mrr from
0.3353 to 0.2078. So every target is probed at its head, middle and tail, and
each question is labelled with where its phrase sits.
"""

from __future__ import annotations

import pytest

from bench.dataset import load_dataset
from bench.probes import POSITIONS, generate, unique_phrase, write
from tests.test_probes import _brain, _family

# Three distinguishing sentences spread across a long body, with shared
# filler between them, so head / middle / tail each have a phrase of their own.
_FILLER = " ".join(["Standing rules are unchanged and nothing here needs action."] * 6)


def _long_family(n: int) -> list[tuple[str, str]]:
    names = ("alpha", "beta", "gamma", "delta", "epsilon")
    return [
        (
            f"Release handoff: checkout-api v2.{i} - state, open items, next steps",
            f"Opening note {names[i - 1]} shard rollout paused overnight. {_FILLER} "
            f"Midway note {names[i - 1]} ledger replay finished cleanly. {_FILLER} "
            f"Closing note {names[i - 1]} exporter left unconfigured deliberately.",
        )
        for i in range(1, n + 1)
    ]


def _rows(conn):
    return conn.execute(
        "SELECT m.id, m.title, m.content, n.name AS ns FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id"
    ).fetchall()


def _phrase(q: dict) -> str:
    return q["query"].removeprefix("what did we say about ")


def test_every_target_is_probed_at_head_middle_and_tail():
    conn = _brain(_long_family(3))
    questions = generate(conn)["questions"]
    by_target: dict[str, dict[str, str]] = {}
    for q in questions:
        by_target.setdefault(q["relevant"][0], {})[q["kind"]] = _phrase(q)
    conn.close()

    assert len(by_target) == 3
    for positions in by_target.values():
        assert list(positions) == list(POSITIONS)
        assert "shard rollout" in positions["head"]
        assert "ledger replay" in positions["middle"]
        assert "exporter left" in positions["tail"]


def test_positions_are_ordered_within_the_memory():
    """head before middle before tail, measured in the content itself."""
    conn = _brain(_long_family(3))
    content = {r["id"]: r["content"].lower() for r in _rows(conn)}
    questions = generate(conn)["questions"]
    conn.close()

    offsets: dict[str, dict[str, int]] = {}
    for q in questions:
        target = q["relevant"][0]
        offsets.setdefault(target, {})[q["kind"]] = content[target].find(_phrase(q).lower())
    for o in offsets.values():
        assert 0 <= o["head"] < o["middle"] < o["tail"]


def test_the_head_probe_is_the_legacy_first_unique_phrase():
    """Continuity with every number of record: the head bucket asks exactly what
    the pre-stratification generator asked, so its figures stay comparable.

    A characterization test: it passes against the old generator by design, and
    pins that the stratification did not move the head bucket."""
    conn = _brain(_long_family(3))
    rows = _rows(conn)
    legacy = {r["id"]: unique_phrase(r, list(rows)) for r in rows}
    questions = generate(conn)["questions"]
    heads = {q["relevant"][0]: _phrase(q) for q in questions if q["kind"] == "head"}
    conn.close()
    assert heads == {k: legacy[k] for k in heads}


def test_a_position_with_no_phrase_of_its_own_is_dropped_not_duplicated():
    """A short memory can have one distinguishing sentence. Asking the same
    phrase three times would triple-weight that target in the aggregate."""
    conn = _brain(_family(3))
    questions = generate(conn)["questions"]
    conn.close()
    pairs = [(q["relevant"][0], _phrase(q)) for q in questions]
    assert len(pairs) == len(set(pairs))


def test_every_positioned_label_is_provably_correct():
    conn = _brain(_long_family(5))
    questions = generate(conn)["questions"]
    for q in questions:
        phrase = _phrase(q).lower()
        holders = [
            r["id"]
            for r in conn.execute("SELECT id, content FROM memories")
            if phrase in r["content"].lower()
        ]
        assert holders == q["relevant"], f"{q['id']}: phrase in {len(holders)} memories"
    conn.close()


@pytest.mark.parametrize("kind", POSITIONS)
def test_positioned_kinds_load_through_the_bench_schema(tmp_path, kind):
    conn = _brain(_long_family(3))
    out = tmp_path / "probes.json"
    write(generate(conn), out)
    conn.close()
    assert any(q.kind == kind for q in load_dataset(out).questions)
