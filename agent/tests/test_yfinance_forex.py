"""Tests for yfinance loader forex support."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.loaders.yfinance_loader import DataLoader, _to_yfinance_symbol


@pytest.mark.parametrize(
    ("project_symbol", "yfinance_symbol"),
    [
        ("EUR/USD", "EURUSD=X"),
        ("eur/usd", "EURUSD=X"),
        ("USD/JPY", "USDJPY=X"),
        ("EURUSD.FX", "EURUSD=X"),
        ("eurusd.fx", "EURUSD=X"),
        ("EURUSD=X", "EURUSD=X"),
    ],
)
def test_to_yfinance_symbol_converts_forex(
    project_symbol: str,
    yfinance_symbol: str,
) -> None:
    assert _to_yfinance_symbol(project_symbol) == yfinance_symbol


def test_forex_is_registered_market() -> None:
    assert "forex" in DataLoader.markets


def _forex_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [1.08120, 1.08210],
            "High": [1.08250, 1.08300],
            "Low": [1.08090, 1.08180],
            "Close": [1.08210, 1.08270],
            "Volume": [0.0, 0.0],
        },
        index=pd.DatetimeIndex(
            ["2026-07-16 10:00:00", "2026-07-16 11:00:00"],
            name="Datetime",
        ),
    )


def test_fetch_converts_forex_and_preserves_original_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backtest.loaders.yfinance_loader as yfl

    monkeypatch.delenv("VIBE_TRADING_DATA_CACHE", raising=False)
    calls = []

    def fake_download(tickers, start_date, end_date, interval):
        calls.append((tickers, start_date, end_date, interval))
        return _forex_frame()

    monkeypatch.setattr(yfl, "_download_history", fake_download)

    result = yfl.DataLoader().fetch(
        ["EUR/USD"],
        "2026-07-15",
        "2026-07-16",
        interval="1H",
    )

    assert "EUR/USD" in result
    assert result["EUR/USD"].iloc[-1]["close"] == pytest.approx(1.08270)
    assert calls == [
        (["EURUSD=X"], "2026-07-15", "2026-07-17", "1h"),
    ]
