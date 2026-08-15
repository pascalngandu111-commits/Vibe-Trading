"""Focused tests for forex-aware swarm grounding."""

from __future__ import annotations

from src.swarm import grounding


def test_extracts_slash_and_fx_suffix_forex_symbols() -> None:
    user_vars = {
        "target": "Validate EUR/USD on the 1H timeframe",
        "comparison": "Compare against GBPUSD.FX",
    }

    assert grounding.extract_symbols_from_user_vars(user_vars) == [
        "EUR/USD",
        "GBPUSD.FX",
    ]


def test_forex_symbols_are_not_split_into_us_ticker_promotions() -> None:
    user_vars = {"goal": "Review EUR/USD while USD remains strong"}

    assert grounding.extract_symbols_from_user_vars(user_vars) == ["EUR/USD"]


def _rows(close: float) -> list[dict]:
    return [
        {
            "trade_date": "2026-07-17T15:00:00",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 0.0,
        }
    ]


def test_formats_non_jpy_forex_with_five_decimals() -> None:
    block = grounding.format_grounding_block({"EUR/USD": _rows(1.08273)})

    assert "1.08273" in block


def test_formats_jpy_forex_with_three_decimals() -> None:
    block = grounding.format_grounding_block({"USD/JPY": _rows(157.4321)})

    assert "157.432" in block
    assert "157.43 " not in block