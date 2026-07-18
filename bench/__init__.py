"""Retrieval benchmark toolset (dev-only, never shipped in the wheel).

Measures gingugu's recall quality with hard math over a golden set of
questions hand-labeled with their relevant memory ids. Design law: truth
status and quality scores are hard-calculated, never LLM-derived — no
LLM-as-judge, ever.

Two tiers:

- **Fixture tier** (CI regression): the dataset carries its own synthetic
  memories; the runner builds an ephemeral in-memory DB and scores against
  it. Deterministic, committable, no real data.
- **Local tier** (ground truth): point ``--db`` at a real brain (opened
  read-only) with a gitignored golden set under ``bench/local/``.

Run: ``uv run python -m bench`` (see ``python -m bench --help``).
"""
