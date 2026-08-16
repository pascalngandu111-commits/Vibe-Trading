#!/usr/bin/env python3
"""Opt-in bounded fixture soak entry point (disabled by default)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.swarm.tradecorefx_acceptance import run_bounded_concurrency


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--soak", action="store_true", help="explicitly enable bounded soak")
    parser.add_argument("--duration-seconds", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not args.soak:
        print(json.dumps({"status":"SKIPPED", "reason":"soak is disabled; pass --soak explicitly"}))
        return 0
    if not 1 <= args.duration_seconds <= 86400 or not 1 <= args.concurrency <= 32:
        parser.error("duration must be 1..86400 seconds and concurrency 1..32")
    started = time.monotonic()
    attempted = 0
    aggregate = []
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]
    while time.monotonic() - started < args.duration_seconds:
        aggregate.append(run_bounded_concurrency(pairs, args.concurrency, _fixture_probe))
        attempted += len(pairs)
    report = {"schema_version":"tradecorefx.soak.v1", "fixture_evidence":True,
              "duration_seconds":args.duration_seconds, "concurrency":args.concurrency,
              "attempted":attempted, "batches":len(aggregate), "status":"COMPLETED_FIXTURE_ONLY"}
    encoded = json.dumps(report, sort_keys=True)
    if args.report: args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


def _fixture_probe(pair: str) -> None:
    """Exercise only the deterministic fixture boundary; never contact a provider."""
    del pair


if __name__ == "__main__": raise SystemExit(main())
