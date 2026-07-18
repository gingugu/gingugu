"""CLI entry point: ``uv run python -m bench``.

Fixture regression run (default dataset, ephemeral DB):
    uv run python -m bench

Real-brain baseline (read-only; golden set lives under bench/local/,
which is gitignored because it describes a private brain):
    uv run python -m bench --dataset bench/local/brain.json \
        --db ~/.local/share/gingugu/memories.db
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from gingugu.config import load_config
from gingugu.embeddings import build_provider

from .dataset import load_dataset
from .runner import (
    DEFAULT_KS,
    FIXTURE_WEIGHTS,
    BenchReport,
    build_fixture_db,
    open_real_db,
    run_benchmark,
)

_FIXTURE = Path(__file__).parent / "datasets" / "fixture.json"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bench", description="Gingugu retrieval benchmark")
    parser.add_argument("--dataset", type=Path, default=_FIXTURE, help="golden-set JSON path")
    parser.add_argument("--db", type=Path, help="real brain DB (opened read-only)")
    parser.add_argument("--k", default=",".join(str(k) for k in DEFAULT_KS), help="cutoffs, csv")
    parser.add_argument(
        "--no-embeddings", action="store_true", help="force BM25-only retrieval on a real DB"
    )
    parser.add_argument("--json", type=Path, help="also write the full report as JSON")
    return parser.parse_args(argv)


def _print_report(report: BenchReport) -> None:
    print(f"dataset:   {report.dataset}")
    print(f"mode:      {report.mode}")
    print(f"retrieval: {report.retrieval}")
    print(f"questions: {len(report.results)}")
    print()
    names = [n for n in report.aggregates if n != "tokens"]
    id_w = max([len("question"), *(len(r.id) for r in report.results)]) + 2
    col_w = max(len(n) for n in names) + 2
    header = f"{'question':<{id_w}}{'kind':<8}"
    header += "".join(f"{n:>{col_w}}" for n in names) + f"{'tokens':>9}"
    print(header)
    print("-" * len(header))
    for r in report.results:
        row = f"{r.id:<{id_w}}{r.kind:<8}"
        row += "".join(f"{r.scores[n]:>{col_w}.3f}" for n in names)
        print(row + f"{r.tokens:>9}")
    print("-" * len(header))
    means = {"all": report.aggregates, **report.by_kind}
    for label, scores in means.items():
        row = f"{'MEAN':<{id_w}}{label:<8}" + "".join(f"{scores[n]:>{col_w}.3f}" for n in names)
        print(row + f"{scores.get('tokens', 0.0):>9.0f}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    ks = tuple(sorted({int(k) for k in args.k.split(",") if k.strip()}))
    if not ks:
        print("error: --k produced no cutoffs", file=sys.stderr)
        return 2

    dataset = load_dataset(args.dataset)
    key_to_id = None
    embedder = None

    if args.db is not None:
        if not args.db.expanduser().exists():
            print(f"error: no DB at {args.db}", file=sys.stderr)
            return 2
        conn = open_real_db(args.db.expanduser())
        cfg = load_config()
        weights, decay_lambda = cfg.weights, cfg.decay_lambda
        if not args.no_embeddings:
            embedder = build_provider(
                cfg.embeddings_enabled,
                model_name=cfg.embeddings_model,
                backend=cfg.embeddings_backend,
                ollama_host=cfg.embeddings_ollama_host,
                ollama_model=cfg.embeddings_ollama_model,
            )
    elif dataset.is_fixture:
        conn, key_to_id = build_fixture_db(dataset)
        weights, decay_lambda = dict(FIXTURE_WEIGHTS), 0.01
    else:
        print("error: dataset has no fixture memories; pass --db", file=sys.stderr)
        return 2

    try:
        report = run_benchmark(
            dataset,
            conn,
            weights=weights,
            decay_lambda=decay_lambda,
            embedder=embedder,
            ks=ks,
            key_to_id=key_to_id,
        )
    finally:
        conn.close()

    _print_report(report)
    if args.json:
        args.json.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        print(f"\nreport written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
