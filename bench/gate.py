"""Replay a real prompt corpus through the involuntary-recall gate.

The gate's unit tests pin its arithmetic. They cannot tell you whether the
thresholds are the RIGHT thresholds, because that question is only answerable
against real prompts and a real brain - neither of which belongs in a public
repository. So the harness is committed and the corpus is not.

Run it:

    uv run python -m bench.gate \\
        --prompts logs/user_prompt_submit.json \\
        --db ~/.local/share/gingugu/memories.db \\
        --namespaces crow,gingugu

``--prompts`` accepts either a Claude Code ``user_prompt_submit.json`` hook log
(a list of event objects carrying a ``prompt`` field) or a plain JSON list of
strings. Both ``logs/`` and ``bench/local/`` are gitignored, which is where a
corpus should live.

What to read in the output: not the firing rate, but **cap3**. A configuration
where most firings hit the cap is not selecting - it is truncating a larger
set, and the ranking inside the cap is doing work the threshold should have
done. Prefer a configuration where cap3 is a small fraction of the firings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gingugu.embeddings import build_provider
from gingugu.recall_gate import GateConfig, is_worth_embedding, select, strip_affect
from gingugu.recall_sweep import connect_readonly, lexical_matches, sweep

DEFAULT_BARS = (0.72, 0.75, 0.78, 0.80, 0.82)
DEFAULT_MARGINS = (0.0, 0.10, 0.15)


def load_prompts(path: Path) -> list[str]:
    """Accept a hook log or a bare list of strings."""
    raw = json.loads(path.read_text())
    if raw and isinstance(raw[0], dict):
        return [e["prompt"] for e in raw if e.get("prompt")]
    return [p for p in raw if isinstance(p, str) and p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.gate")
    parser.add_argument("--prompts", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--namespaces", default="crow")
    parser.add_argument("--cap", type=int, default=3)
    parser.add_argument("--samples", type=int, default=0, help="print N example hits")
    parser.add_argument("--no-lexical", action="store_true", help="semantic only")
    args = parser.parse_args(argv)

    namespaces = [n.strip() for n in args.namespaces.split(",") if n.strip()]
    prompts = load_prompts(args.prompts.expanduser())
    survivors = [p for p in prompts if is_worth_embedding(p)]

    print(f"corpus            : {len(prompts)} prompts")
    print(
        f"stage 1 rejected  : {len(prompts) - len(survivors)} "
        f"({100 * (len(prompts) - len(survivors)) / max(len(prompts), 1):.0f}%) "
        "- never reach the encoder"
    )

    provider = build_provider(True)
    if not provider.enabled:
        print("no embedding provider available", file=sys.stderr)
        return 1

    cleaned = [strip_affect(p)[:2000] for p in survivors]
    vectors = provider.encode_many(cleaned)

    conn = connect_readonly(args.db.expanduser())
    try:
        # Sweep once per prompt at a permissive config, then re-gate in memory:
        # the encoder and the SQL are the expensive half, and the thresholds
        # being swept are pure arithmetic over the result.
        base = GateConfig(bar=0.0, margin=0.0, cap=10**6, require_lexical=False)
        sweeps = []
        for text, vec in zip(cleaned, vectors, strict=True):
            if not vec:
                continue
            cands = sweep(conn, list(vec), namespaces, config=base)
            lex = None if args.no_lexical else lexical_matches(conn, text, namespaces)
            sweeps.append((text, cands, lex))
    finally:
        conn.close()

    print(f"swept             : {len(sweeps)} prompts against {namespaces}\n")
    header = f"{'bar':>5} {'margin':>7} {'fires':>7} {'% turns':>8} {'cap3':>6} {'mean':>6}"
    print(header)
    print("-" * len(header))
    for margin in DEFAULT_MARGINS:
        for bar in DEFAULT_BARS:
            cfg = GateConfig(
                bar=bar, margin=margin, cap=args.cap, require_lexical=not args.no_lexical
            )
            fires = capped = total = 0
            for _, cands, lex in sweeps:
                picked = select(cands, lexical_ids=lex, config=cfg)
                if picked:
                    fires += 1
                    total += len(picked)
                    capped += len(picked) == cfg.cap
            pct = 100 * fires / max(len(prompts), 1)
            mean = total / max(fires, 1)
            print(
                f"{bar:>5.2f} {margin:>7.2f} {fires:>7} {pct:>7.0f}% " f"{capped:>6} {mean:>6.2f}"
            )

    if args.samples:
        cfg = GateConfig(cap=args.cap, require_lexical=not args.no_lexical)
        print(f"\nsamples at the shipped defaults (bar {cfg.bar}, margin {cfg.margin}):")
        shown = 0
        for text, cands, lex in sweeps:
            picked = select(cands, lexical_ids=lex, config=cfg)
            if picked and shown < args.samples:
                top = picked[0]
                print(f"  {top.similarity:.3f} [{top.type}] {text[:60]!r}")
                print(f"        -> {top.title[:78]}")
                shown += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
