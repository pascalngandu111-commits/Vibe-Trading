"""Deterministic technical features for TradeCoreFX grounding data.

The swarm prompt shows only a compact tail of each timeframe. This module
calculates indicators from the full persisted OHLCV window before an LLM sees
the data, so agents never need to estimate RSI, MACD, ATR, or moving averages
from a few displayed bars.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

import pandas as pd


_OHLC_COLUMNS = ("open", "high", "low", "close")


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Return Wilder-EWM RSI, with explicit flat/up/down edge handling."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((avg_gain > 0) & (avg_loss == 0), 100.0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0.0)
    return rsi.mask((avg_gain == 0) & (avg_loss == 0), 50.0)


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.DataFrame:
    """Return ADX and directional indicators using Wilder-EWM smoothing."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    alpha = 1 / period
    smoothed_tr = true_range.ewm(alpha=alpha, min_periods=period).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=alpha, min_periods=period).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=alpha, min_periods=period).mean()
    plus_di = 100 * smoothed_plus_dm / smoothed_tr
    minus_di = 100 * smoothed_minus_dm / smoothed_tr
    di_sum = plus_di + minus_di
    di_sum = di_sum.mask(di_sum == 0)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, min_periods=period).mean()
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx})


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Return Average True Range using Wilder-EWM smoothing."""
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period).mean()


def compute_bollinger(
    close: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Return Bollinger middle, upper, and lower bands."""
    middle = close.rolling(window).mean()
    standard_deviation = close.rolling(window).std()
    return pd.DataFrame(
        {
            "middle": middle,
            "upper": middle + num_std * standard_deviation,
            "lower": middle - num_std * standard_deviation,
        }
    )


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Return standard EMA MACD line, signal line, and histogram."""
    fast_ema = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    slow_ema = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = fast_ema - slow_ema
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "line": line,
            "signal": signal_line,
            "histogram": line - signal_line,
        }
    )


def _latest_number(series: pd.Series) -> float | None:
    """Return the final finite value, otherwise ``None``."""
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _period_return(close: pd.Series, periods: int) -> float | None:
    """Return percentage change over *periods* bars when available."""
    if len(close) <= periods:
        return None
    previous = float(close.iloc[-periods - 1])
    if previous == 0:
        return None
    return (float(close.iloc[-1]) / previous - 1.0) * 100.0


def _rows_to_frame(rows: Iterable[Mapping[str, object]]) -> tuple[pd.DataFrame, int]:
    """Normalize grounding rows and count invalid bars removed."""
    raw = list(rows)
    if not raw:
        return pd.DataFrame(), 0

    frame = pd.DataFrame(raw)
    required = {"trade_date", *_OHLC_COLUMNS}
    if not required.issubset(frame.columns):
        return pd.DataFrame(), len(raw)

    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce", utc=True)
    for column in (*_OHLC_COLUMNS, "volume"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    valid = frame["trade_date"].notna()
    valid &= frame[list(_OHLC_COLUMNS)].notna().all(axis=1)
    valid &= frame["high"] >= frame[["open", "close"]].max(axis=1)
    valid &= frame["low"] <= frame[["open", "close"]].min(axis=1)
    valid &= frame["high"] >= frame["low"]
    valid &= frame[list(_OHLC_COLUMNS)].gt(0).all(axis=1)

    normalized = frame.loc[valid].copy()
    normalized = normalized.sort_values("trade_date")
    normalized = normalized.drop_duplicates(subset=["trade_date"], keep="last")
    normalized = normalized.set_index("trade_date")
    dropped = len(raw) - len(normalized)
    return normalized, dropped


def _trend_alignment(
    close: float,
    sma20: float | None,
    sma50: float | None,
    sma200: float | None,
) -> str:
    """Classify deterministic moving-average alignment."""
    if sma20 is None or sma50 is None:
        return "insufficient_data"
    bullish = close > sma20 > sma50
    bearish = close < sma20 < sma50
    if sma200 is not None:
        bullish = bullish and sma50 > sma200
        bearish = bearish and sma50 < sma200
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    return "mixed"


def _momentum_alignment(rsi: float | None, macd_histogram: float | None) -> str:
    """Classify momentum only when both deterministic inputs exist."""
    if rsi is None or macd_histogram is None:
        return "insufficient_data"
    if rsi >= 50.0 and macd_histogram > 0:
        return "bullish"
    if rsi < 50.0 and macd_histogram < 0:
        return "bearish"
    return "mixed"


def build_timeframe_snapshot(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Calculate a deterministic evidence snapshot for one timeframe."""
    source_rows = list(rows)
    frame, dropped = _rows_to_frame(source_rows)
    if frame.empty:
        return {
            "bars": 0,
            "dropped_invalid_bars": dropped,
            "status": "insufficient_data",
        }

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    latest = frame.iloc[-1]
    last_source_row = source_rows[-1] if source_rows else {}

    sma20 = _latest_number(close.rolling(20).mean())
    sma50 = _latest_number(close.rolling(50).mean())
    sma200 = _latest_number(close.rolling(200).mean())
    ema12 = _latest_number(close.ewm(span=12, adjust=False, min_periods=12).mean())
    ema26 = _latest_number(close.ewm(span=26, adjust=False, min_periods=26).mean())
    rsi14 = _latest_number(compute_rsi(close, 14))
    macd = compute_macd(close)
    macd_line = _latest_number(macd["line"])
    macd_signal = _latest_number(macd["signal"])
    macd_histogram = _latest_number(macd["histogram"])
    atr14 = _latest_number(compute_atr(high, low, close, 14))
    adx = compute_adx(high, low, close, 14)
    adx14 = _latest_number(adx["adx"])
    plus_di14 = _latest_number(adx["plus_di"])
    minus_di14 = _latest_number(adx["minus_di"])
    bollinger = compute_bollinger(close)
    bb_middle = _latest_number(bollinger["middle"])
    bb_upper = _latest_number(bollinger["upper"])
    bb_lower = _latest_number(bollinger["lower"])
    latest_close = float(latest["close"])

    atr_percent = None
    if atr14 is not None and latest_close:
        atr_percent = atr14 / latest_close * 100.0
    bb_width_percent = None
    if bb_upper is not None and bb_lower is not None and bb_middle:
        bb_width_percent = (bb_upper - bb_lower) / bb_middle * 100.0

    volume_available = bool((frame["volume"].fillna(0.0) > 0).any())
    alignment = _trend_alignment(latest_close, sma20, sma50, sma200)
    momentum = _momentum_alignment(rsi14, macd_histogram)
    if len(frame) < 35:
        indicator_status = "insufficient_data"
    elif len(frame) < 200:
        indicator_status = "partial_history"
    else:
        indicator_status = "ok"

    return {
        "bars": len(frame),
        "dropped_invalid_bars": dropped,
        "status": indicator_status,
        "as_of": frame.index[-1].isoformat(),
        "source": last_source_row.get("source", "unknown"),
        "timeframe": last_source_row.get("timeframe", "unknown"),
        "is_stale": bool(last_source_row.get("is_stale", False)),
        "volume_available": volume_available,
        "latest": {
            "open": float(latest["open"]),
            "high": float(latest["high"]),
            "low": float(latest["low"]),
            "close": latest_close,
        },
        "returns_pct": {
            "1_bar": _period_return(close, 1),
            "5_bar": _period_return(close, 5),
            "20_bar": _period_return(close, 20),
        },
        "trend": {
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "ema12": ema12,
            "ema26": ema26,
            "alignment": alignment,
        },
        "momentum": {
            "rsi14": rsi14,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "alignment": momentum,
        },
        "volatility": {
            "atr14": atr14,
            "atr_percent": atr_percent,
            "bb_middle": bb_middle,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_width_percent": bb_width_percent,
        },
        "directional": {
            "adx14": adx14,
            "plus_di14": plus_di14,
            "minus_di14": minus_di14,
        },
        "levels": {
            "high_20": float(high.tail(20).max()),
            "low_20": float(low.tail(20).min()),
            "high_50": float(high.tail(50).max()),
            "low_50": float(low.tail(50).min()),
        },
    }


def build_multitimeframe_snapshots(
    grounding: Mapping[str, list[dict]],
) -> dict[str, dict[str, dict[str, object]]]:
    """Build symbol/timeframe snapshots from persisted grounding rows."""
    result: dict[str, dict[str, dict[str, object]]] = {}
    for symbol, rows in grounding.items():
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            timeframe = str(row.get("timeframe") or "1D")
            grouped[timeframe].append(row)
        if grouped:
            result[symbol] = {
                timeframe: build_timeframe_snapshot(timeframe_rows)
                for timeframe, timeframe_rows in grouped.items()
            }
    return result
