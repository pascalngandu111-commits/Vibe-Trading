"""Runtime wiring tests for TradeCoreFX multi-timeframe grounding."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.swarm import grounding
from src.swarm.runtime import SwarmRuntime


class _NoopHeartbeatTimer:
    """Context manager stub that avoids starting a timer thread."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def test_runtime_prefetch_uses_multitimeframe_fetch_and_persists(monkeypatch) -> None:
    import src.agent.progress as progress

    monkeypatch.setattr(progress, "HeartbeatTimer", _NoopHeartbeatTimer)
    monkeypatch.setattr(grounding, "max_grounding_symbols", lambda: 8)

    expected = {
        "EUR/USD": [
            {
                "trade_date": "2026-07-17T15:00:00",
                "open": 1.08200,
                "high": 1.08750,
                "low": 1.07900,
                "close": 1.08450,
                "volume": 0.0,
                "timeframe": "1H",
                "source": "yfinance",
                "fetched_at": "2026-07-17T17:00:00+00:00",
                "market": "forex",
                "is_stale": False,
            }
        ]
    }
    captured_symbols: list[list[str]] = []

    def fake_multitimeframe_fetch(symbols):
        captured_symbols.append(list(symbols))
        return expected

    def fail_legacy_fetch(*args, **kwargs):
        raise AssertionError("runtime must not call legacy daily-only grounding")

    monkeypatch.setattr(
        grounding,
        "fetch_multitimeframe_grounding_data",
        fake_multitimeframe_fetch,
    )
    monkeypatch.setattr(grounding, "fetch_grounding_data", fail_legacy_fetch)

    runtime = object.__new__(SwarmRuntime)
    runtime._store = MagicMock()
    runtime._emit_event = MagicMock()
    runtime._make_event = MagicMock()
    run = SimpleNamespace(
        id="run-tradecorefx",
        user_vars={"goal": "Analyze EUR/USD across 1D, 4H, and 1H"},
        grounding_data=None,
    )

    runtime._prefetch_grounding_data(run)

    assert captured_symbols == [["EUR/USD"]]
    assert run.grounding_data == expected
    runtime._store.update_run.assert_called_once_with(run)
