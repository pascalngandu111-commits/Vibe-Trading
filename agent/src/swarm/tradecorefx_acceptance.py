"""Bounded deterministic operations harness; no live acceptance is implied."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Iterable

from src.swarm.tradecorefx_validation import normalize_forex_pair


SAFE_PROVIDER_STATES = {"success": "READY", "timeout": "WAIT", "throttling": "WAIT", "schema_drift": "NO_TRADE_DATA", "partial_timeframe_failure": "NO_TRADE_DATA", "mixed_providers": "NO_TRADE_DATA", "stale_data": "NO_TRADE_DATA"}


@dataclass(frozen=True)
class AcceptanceReport:
    schema_version: str
    started_at: str
    duration_seconds: int
    concurrency: int
    attempted: int
    completed: int
    failed: int
    evidence_kind: str = "FIXTURE_ONLY"


def load_provider_fixture(path: Path) -> dict:
    if path.stat().st_size > 16_384:
        raise ValueError("Provider fixture exceeds the bounded contract")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Untrusted provider fixture")
    scenario = payload.get("scenario")
    if (
        payload.get("evidence_kind") != "RECORDED_CONTRACT_FIXTURE"
        or payload.get("live") is not False
        or scenario not in SAFE_PROVIDER_STATES
    ):
        raise ValueError("Untrusted provider fixture")
    return {"scenario": scenario, "safe_state": SAFE_PROVIDER_STATES[scenario], "live": False}


def run_bounded_concurrency(pairs: Iterable[str], worker_capacity: int, runner: Callable[[str], object]) -> dict:
    selected = [normalize_forex_pair(pair) for pair in pairs]
    if not 1 <= worker_capacity <= 32 or not 1 <= len(selected) <= 128:
        raise ValueError("Harness limits exceeded")
    if any(pair is None for pair in selected) or len(set(selected)) != len(selected):
        raise ValueError("Harness pairs must be unique supported beta FX pairs")
    failures = []
    with ThreadPoolExecutor(max_workers=worker_capacity) as pool:
        futures = {pool.submit(runner, pair): pair for pair in selected}
        for future in as_completed(futures):
            try: future.result()
            except Exception: failures.append(futures[future])
    report = AcceptanceReport(
        schema_version="tradecorefx.acceptance.v1",
        started_at=datetime.now(timezone.utc).isoformat(), duration_seconds=0,
        concurrency=worker_capacity, attempted=len(selected),
        completed=len(selected) - len(failures), failed=len(failures),
    )
    return {**asdict(report), "failed_pairs": failures}
