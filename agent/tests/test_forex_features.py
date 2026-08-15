"""Tests for deterministic TradeCoreFX technical feature snapshots."""

from __future__ import annotations

import pandas as pd
import pytest

from src.swarm.forex_features import (
    build_multitimeframe_snapshots,
    build_timeframe_snapshot,
    compute_rsi,
)


def _trend_rows(count: int, timeframe: str = "1H") -> list[dict]:
    timestamps = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
    rows: list[dict] = []
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
                "timeframe": timeframe,
                "source": "yfinance",
                "fetched_at": "2026-07-17T17:00:00+00:00",
                "market": "forex",
                "is_stale": False,
            }
        )
    return rows


def test_snapshot_calculates_full_indicator_evidence() -> None:
    snapshot = build_timeframe_snapshot(_trend_rows(260))

    assert snapshot["status"] == "ok"
    assert snapshot["bars"] == 260
    assert snapshot["dropped_invalid_bars"] == 0
    assert snapshot["source"] == "yfinance"
    assert snapshot["timeframe"] == "1H"
    assert snapshot["is_stale"] is False
    assert snapshot["volume_available"] is False

    trend = snapshot["trend"]
    assert trend["alignment"] == "bullish"
    assert trend["sma20"] is not None
    assert trend["sma50"] is not None
    assert trend["sma200"] is not None
    assert trend["ema12"] is not None
    assert trend["ema26"] is not None

    momentum = snapshot["momentum"]
    assert momentum["rsi14"] == pytest.approx(100.0)
    assert momentum["macd_line"] is not None
    assert momentum["macd_signal"] is not None
    assert momentum["macd_histogram"] is not None
    assert momentum["alignment"] == "bullish"

    volatility = snapshot["volatility"]
    assert volatility["atr14"] is not None
    assert volatility["atr_percent"] > 0
    assert volatility["bb_upper"] > volatility["bb_middle"]
    assert volatility["bb_middle"] > volatility["bb_lower"]

    directional = snapshot["directional"]
    assert directional["adx14"] is not None
    assert directional["plus_di14"] > directional["minus_di14"]
    assert snapshot["returns_pct"]["20_bar"] > 0


def test_rsi_handles_flat_series_as_neutral() -> None:
    close = pd.Series([1.1] * 30)

    assert compute_rsi(close, 14).iloc[-1] == pytest.approx(50.0)


def test_snapshot_reports_partial_and_insufficient_history() -> None:
    insufficient = build_timeframe_snapshot(_trend_rows(20))
    partial = build_timeframe_snapshot(_trend_rows(100))

    assert insufficient["status"] == "insufficient_data"
    assert insufficient["trend"]["sma50"] is None
    assert insufficient["momentum"]["macd_histogram"] is None
    assert partial["status"] == "partial_history"
    assert partial["trend"]["sma50"] is not None
    assert partial["trend"]["sma200"] is None


def test_snapshot_drops_invalid_ohlc_bar() -> None:
    rows = _trend_rows(50)
    rows.append(
        {
            **rows[-1],
            "trade_date": "2026-07-17T18:00:00+00:00",
            "open": 1.10,
            "high": 1.09,
            "low": 1.08,
            "close": 1.11,
        }
    )

    snapshot = build_timeframe_snapshot(rows)

    assert snapshot["bars"] == 50
    assert snapshot["dropped_invalid_bars"] == 1


def test_builds_separate_snapshots_for_each_timeframe() -> None:
    grounding = {
        "EUR/USD": _trend_rows(260, "1D") + _trend_rows(260, "1H"),
    }

    snapshots = build_multitimeframe_snapshots(grounding)

    assert set(snapshots["EUR/USD"]) == {"1D", "1H"}
    assert snapshots["EUR/USD"]["1D"]["bars"] == 260
    assert snapshots["EUR/USD"]["1H"]["bars"] == 260
