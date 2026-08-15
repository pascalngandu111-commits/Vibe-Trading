"""Integration tests for deterministic features in grounding prompts."""

from __future__ import annotations

import pandas as pd

from src.swarm import grounding


def _grounding_rows(count: int = 260) -> list[dict]:
    rows: list[dict] = []
    timestamps = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
    for index, timestamp in enumerate(timestamps):
        close = 1.05000 + index * 0.00010 + index * index * 0.0000002
        rows.append(
            {
                "trade_date": timestamp.isoformat(),
                "open": close - 0.00010,
                "high": close + 0.00100,
                "low": close - 0.00100,
                "close": close,
                "volume": 0.0,
                "timeframe": "1H",
                "source": "yfinance",
                "fetched_at": "2026-07-17T17:00:00+00:00",
                "market": "forex",
                "is_stale": False,
            }
        )
    return rows


def test_grounding_prompt_includes_deterministic_indicator_evidence() -> None:
    block = grounding.format_grounding_block({"EUR/USD": _grounding_rows()})

    assert "Deterministic indicator evidence" in block
    assert "computed from the complete bar windows" in block
    assert "RSI14" in block
    assert "MACD hist" in block
    assert "ADX/+DI/-DI" in block
    assert "ATR14 (% close)" in block
    assert "SMA20" in block
    assert "SMA50" in block
    assert "SMA200" in block
    assert "bullish" in block
    assert "100.00" in block


def test_grounding_prompt_rejects_spot_fx_volume_inference() -> None:
    block = grounding.format_grounding_block({"EUR/USD": _grounding_rows()})

    assert "no reliable centralized volume" in block
    assert "Do not claim OBV" in block
    assert "zero-filled volume bars" in block
