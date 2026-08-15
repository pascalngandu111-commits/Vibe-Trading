"""Focused tests for source-labelled multi-timeframe swarm grounding."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.swarm import grounding


class _StubLoader:
    """Return deterministic OHLCV while recording requested timeframes."""

    name = "stub-feed"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, str, str]] = []

    def fetch(self, codes, start_date, end_date, *, interval="1D"):
        self.calls.append((list(codes), start_date, end_date, interval))
        index = pd.to_datetime([
            "2026-07-17T13:00:00",
            "2026-07-17T15:00:00",
        ])
        frame = pd.DataFrame(
            {
                "open": [1.08000, 1.08200],
                "high": [1.08500, 1.08750],
                "low": [1.07500, 1.07900],
                "close": [1.08200, 1.08450],
                "volume": [0.0, 0.0],
            },
            index=index,
        )
        return {code: frame for code in codes}


def test_fetches_three_fx_timeframes_with_metadata(monkeypatch) -> None:
    loader = _StubLoader()
    monkeypatch.setattr(
        grounding,
        "_resolve_grounding_loader",
        lambda market, timeframe: loader,
    )

    captured_at = datetime(2026, 7, 17, 17, 0, tzinfo=timezone.utc)
    result = grounding.fetch_multitimeframe_grounding_data(
        ["EUR/USD"],
        now=captured_at,
    )

    assert [call[3] for call in loader.calls] == ["1D", "4H", "1H"]
    rows = result["EUR/USD"]
    assert {row["timeframe"] for row in rows} == {"1D", "4H", "1H"}
    assert {row["source"] for row in rows} == {"stub-feed"}
    assert {row["market"] for row in rows} == {"forex"}
    assert {row["fetched_at"] for row in rows} == {captured_at.isoformat()}
    assert all(isinstance(row["is_stale"], bool) for row in rows)


def test_non_forex_symbols_remain_daily_only(monkeypatch) -> None:
    loader = _StubLoader()
    monkeypatch.setattr(
        grounding,
        "_resolve_grounding_loader",
        lambda market, timeframe: loader,
    )

    grounding.fetch_multitimeframe_grounding_data(
        ["NVDA.US"],
        now=datetime(2026, 7, 17, 17, 0, tzinfo=timezone.utc),
    )

    assert [call[3] for call in loader.calls] == ["1D"]


def test_freshness_check_allows_normal_forex_weekend_gap() -> None:
    last_friday_bar = "2026-07-17T21:00:00"
    sunday = datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc)

    assert grounding._is_stale_bar(
        last_friday_bar,
        "1H",
        sunday,
        "forex",
    ) is False


def test_freshness_check_marks_old_intraday_bar_stale() -> None:
    assert grounding._is_stale_bar(
        "2026-07-17T08:00:00",
        "1H",
        datetime(2026, 7, 17, 17, 0, tzinfo=timezone.utc),
        "forex",
    ) is True


def _multi_row(
    timeframe: str,
    trade_date: str,
    *,
    high: float,
    low: float,
    close: float,
    stale: bool,
) -> dict:
    return {
        "trade_date": trade_date,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 0.0,
        "timeframe": timeframe,
        "source": "yfinance",
        "fetched_at": "2026-07-17T17:00:00+00:00",
        "market": "forex",
        "is_stale": stale,
    }


def test_formatter_shows_source_freshness_and_true_high_low_range() -> None:
    rows = [
        _multi_row(
            "1D",
            "2026-07-16T00:00:00",
            high=1.10000,
            low=1.07000,
            close=1.08500,
            stale=False,
        ),
        _multi_row(
            "1D",
            "2026-07-17T00:00:00",
            high=1.09500,
            low=1.07500,
            close=1.09000,
            stale=False,
        ),
        _multi_row(
            "1H",
            "2026-07-17T15:00:00",
            high=1.08750,
            low=1.07900,
            close=1.08450,
            stale=True,
        ),
    ]

    block = grounding.format_grounding_block({"EUR/USD": rows})

    assert "Multi-Timeframe Market Data" in block
    assert "Captured:" in block
    assert "yfinance" in block
    assert "FRESH" in block
    assert "STALE" in block
    assert "1.07000 - 1.10000" in block
    assert "1.08450" in block
    assert "broker-executable quotes" in block
