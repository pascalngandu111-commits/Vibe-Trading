"""Pre-fetch market data for symbols mentioned in a swarm's user_vars.

Why this exists
---------------
Swarm workers are LLMs. Without explicit grounding they cheerfully quote
prices from their training data - which is wrong by definition for any
asset that has traded since the model's cutoff. The fix can only be
structural: feed the worker the real recent prices before it starts
reasoning, and tell it those are the only prices it may cite.

What this module does
---------------------
* Scans every value in ``user_vars`` for tokens that match one of the
  data-source-suffixed symbol shapes the loaders already understand
  (``NVDA.US``, ``700.HK``, ``600519.SH``, ``BTC-USDT``, etc.).
* Pulls the last ``DEFAULT_WINDOW_DAYS`` of OHLCV for each detected
  symbol via ``backtest.loaders.registry.resolve_loader`` with
  ``source="auto"``. Failures (delisted ticker, network blip) are
  swallowed per-symbol so they do not poison the whole run.
* Renders a compact markdown block the worker prompt can splice in.

Bare US tickers
---------------
Suffixed symbols are matched verbatim. Bare all-caps tokens (``NVDA``
without ``.US``) are *promoted* to ``<TOKEN>.US`` under guards, because
auto-built swarm variables routinely carry the user's raw prompt and
real prompts say "long or short on NVDA", not "NVDA.US" (#198):

* only 2-5 uppercase letters on word boundaries (never lowercase,
  never single letters - too collision-prone);
* a stopword list drops common finance/English acronyms (``ETF``,
  ``CEO``, ``GDP``, ``USD``, bare crypto symbols, etc.);
* text already matched by a suffixed pattern is blanked first, so
  ``BTC-USDT`` never leaks a bogus ``BTC.US``;
* promotions sort *after* explicit symbols, so explicit symbols win
  the ``DEFAULT_MAX_SYMBOLS`` cap;
* the per-symbol fetch remains the final validator - a promoted token
  that is not a real Yahoo ticker returns no data and is dropped.

A residual risk stays by design: an all-caps non-ticker word that
collides with a real listed product (e.g. ``MOAT``) grounds an
irrelevant table. That costs prompt budget, not correctness - workers
are told to cite only symbols they analyze.
* It does not refresh data mid-run. The block is a snapshot taken once
  when the background run starts; long-running swarms will see stale data after
  many minutes, but that is still strictly better than training-data
  prices from a year ago.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)


# Window of OHLCV bars to fetch per symbol. 30 calendar days yields
# roughly 21 US trading days - enough for a "recent" view without
# bloating the worker prompt.
DEFAULT_WINDOW_DAYS = 30
DEFAULT_MAX_SYMBOLS = 8
MAX_SYMBOLS_ENV = "SWARM_GROUNDING_MAX_SYMBOLS"

# How many of the most-recent rows to render in the worker prompt.
# The full window is still used to compute the min/max line; the table
# is truncated for readability.
PROMPT_TABLE_TAIL = 5
MULTITIMEFRAME_PROMPT_TAIL = 3

# TradeCoreFX starts with three decision horizons. The longer daily window
# gives indicators such as a 200-day moving average enough history, while the
# intraday windows stay within public-provider limits and prompt budgets.
DEFAULT_FX_TIMEFRAME_WINDOWS: dict[str, int] = {
    "1D": 365,
    "4H": 60,
    "1H": 14,
}

# A public bar older than these limits is explicitly labelled stale. Forex
# receives weekend grace in ``_is_stale_bar`` because the market is closed.
STALE_AFTER_HOURS: dict[str, float] = {
    "1D": 72.0,
    "4H": 12.0,
    "1H": 4.0,
}

# Symbol patterns understood by the bundled loaders. Anchored on word
# boundaries so substrings of longer text don't trigger.
_SYMBOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z]{1,5}\.US\b"),
    re.compile(r"\b\d{3,5}\.HK\b"),
    re.compile(r"\b\d{6}\.(?:SZ|SH|BJ)\b"),
    re.compile(r"(?<![A-Z])[A-Z]{3}/[A-Z]{3}(?![A-Z])"),
    re.compile(r"\b[A-Z]{6}\.FX\b"),
    re.compile(r"\b[A-Z]{2,6}-USDT\b"),
)

# Bare-ticker promotion: 2-5 uppercase letters. Single letters (A, F, T, etc.)
# collide with ordinary prose far too often to be worth grounding. The
# lookarounds reject dotted compounds on either side (FOO.USDA promotes
# neither FOO nor USDA) while still matching a sentence-ending "NVDA.".
_BARE_US_TICKER_PATTERN = re.compile(r"(?<![\w.])[A-Z]{2,5}(?!\w)(?!\.\w)")

# All-caps tokens that show up in finance prompts but must never be promoted
# to a .US symbol - either not tickers at all, or colliding with unrelated
# listed products (CEO and MSCI are both real Yahoo symbols).
_BARE_TICKER_STOPWORDS = frozenset({
    # geography / venues / index & data providers
    "US", "USA", "UK", "EU", "HK", "CN", "JP", "NYSE", "AMEX", "SSE", "SZSE",
    "HKEX", "SPX", "NDX", "DJI", "DJIA", "HSI", "CSI", "FTSE", "MSCI", "VIX",
    # instruments / structures
    "ETF", "ETN", "ADR", "IPO", "REIT", "BOND", "SWAP", "PERP",
    # macro / institutions
    "FED", "FOMC", "SEC", "IMF", "GDP", "CPI", "PPI", "PMI", "PCE", "OPEC",
    "YOY", "QOQ", "MOM", "YTD", "EOD",
    # metrics / indicators
    "PE", "PB", "PS", "EPS", "ROE", "ROA", "ROI", "EBIT", "EV", "DCF",
    "CAGR", "IRR", "NAV", "AUM", "ATH", "ATL", "RSI", "MACD", "EMA", "SMA",
    "KDJ", "BOLL", "OHLC", "ADV", "PNL",
    # currencies / crypto traded under other loaders
    "USD", "EUR", "JPY", "GBP", "CNY", "CNH", "RMB", "KRW", "INR", "AUD",
    "CAD", "CHF", "FX", "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE",
    "USDT", "USDC", "DEFI", "NFT", "DAO",
    # trading verbs / order words
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "CALL", "PUT", "STOP", "LIMIT",
    "TP", "SL", "DCA",
    # tech / prose acronyms
    "AI", "ML", "LLM", "API", "JSON", "CSV", "PDF", "URL", "HTML", "CEO",
    "CFO", "CTO", "COO", "CIO", "VP", "OK", "FAQ", "ASAP", "AM", "PM",
    "EST", "PST", "UTC", "GMT",
})


def _price_decimals(code: str) -> int:
    """Return display precision appropriate for the project symbol."""
    upper = code.upper()
    if re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", upper):
        quote_currency = upper[-3:]
        return 3 if quote_currency == "JPY" else 5
    if re.fullmatch(r"[A-Z]{6}\.FX", upper):
        quote_currency = upper[3:6]
        return 3 if quote_currency == "JPY" else 5
    return 2


def _as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating naive inputs as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_bar_timestamp(value: str) -> datetime:
    """Parse a loader ISO timestamp as UTC for freshness comparisons."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def _is_stale_bar(
    trade_date: str,
    timeframe: str,
    captured_at: datetime,
    market: str,
) -> bool:
    """Return whether the latest bar is too old to represent current context.

    The check is deliberately conservative. It labels data; it never deletes
    historical bars. Forex gets 48 hours of grace during the weekend so a
    normal Friday close is not incorrectly called stale on Saturday/Sunday.
    """
    maximum_age = STALE_AFTER_HOURS.get(timeframe, 72.0)
    captured_utc = _as_utc(captured_at)
    if market == "forex" and captured_utc.weekday() in {0, 5, 6}:
        maximum_age += 48.0
    age_hours = max(
        0.0,
        (captured_utc - _parse_bar_timestamp(trade_date)).total_seconds() / 3600.0,
    )
    return age_hours > maximum_age


def _resolve_grounding_loader(market: str, timeframe: str):
    """Resolve a loader suitable for the requested grounding timeframe.

    AKShare's forex adapter is daily-only and silently ignores ``interval``.
    Use the yfinance adapter validated by TradeCoreFX for all FX timeframes so
    1H/4H requests cannot masquerade as intraday while returning daily bars.
    Other markets retain the project's normal registry routing.
    """
    if market == "forex":
        from backtest.loaders.yfinance_loader import DataLoader

        return DataLoader()

    from backtest.loaders.registry import resolve_loader

    return resolve_loader(market)


def extract_symbols_from_user_vars(user_vars: dict[str, str]) -> list[str]:
    """Return the deduplicated list of symbols mentioned anywhere in *user_vars*.

    Explicit suffixed symbols come first (in first-occurrence order),
    followed by guarded bare-ticker promotions (``NVDA`` to ``NVDA.US``),
    so explicit symbols always win the grounding cap. See the module
    docstring for the promotion guards.
    """
    explicit: dict[str, None] = {}  # ordered set
    promoted: dict[str, None] = {}
    for value in user_vars.values():
        if not isinstance(value, str):
            continue
        remainder = value
        for pattern in _SYMBOL_PATTERNS:
            for match in pattern.findall(remainder):
                explicit.setdefault(match, None)
            # Blank matched spans so the bare scan can't split a suffixed
            # symbol into bogus fragments (BTC-USDT -> BTC.US).
            remainder = pattern.sub(" ", remainder)
        for token in _BARE_US_TICKER_PATTERN.findall(remainder):
            if token not in _BARE_TICKER_STOPWORDS:
                promoted.setdefault(f"{token}.US", None)
    return list(explicit) + [s for s in promoted if s not in explicit]


def max_grounding_symbols() -> int:
    """Return the configured cap for symbols fetched into worker prompts."""
    value = get_env_config().swarm.swarm_grounding_max_symbols
    return max(1, value)


def fetch_grounding_data(
    symbols: Iterable[str],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
) -> dict[str, list[dict]]:
    """Fetch OHLCV for *symbols* and return a code -> list-of-bars mapping.

    Each bar is a plain dict with ``trade_date`` (ISO string), ``open``,
    ``high``, ``low``, ``close``, ``volume``. Symbols that fail to
    resolve are simply omitted from the result with a logged warning.

    Args:
        symbols: Iterable of suffixed symbols (``NVDA.US`` etc.).
        window_days: Calendar-day lookback. Defaults to
            :data:`DEFAULT_WINDOW_DAYS`.
        today: Override the upper bound (mainly for tests). Defaults to
            ``date.today()``.

    Returns:
        Dict keyed by the *original* symbol string with the bars list as
        value. Empty if no symbols resolve.
    """
    symbols_list = list(symbols)
    if not symbols_list:
        return {}

    end = today or date.today()
    start = end - timedelta(days=window_days)
    start_str = start.isoformat()
    end_str = end.isoformat()

    # Imported lazily so unit tests of the extraction / formatting layer
    # don't have to drag in pandas + the loader graph just to import.
    # ``resolve_loader`` expects a *market* key (``"us_equity"`` etc.), not a
    # raw code; ``_detect_market`` is the function ``runner.py`` already uses
    # to dispatch the same shapes we extract here, so reusing it keeps the
    # routing identical to the rest of the codebase.
    from backtest.loaders.registry import resolve_loader
    from backtest.runner import _detect_market

    out: dict[str, list[dict]] = {}
    for code in symbols_list:
        try:
            market = _detect_market(code)
            loader = resolve_loader(market)  # already a ready-to-use instance
            df_map = loader.fetch([code], start_str, end_str, interval="1D")
        except Exception as exc:  # pragma: no cover - depends on network
            logger.warning(
                "grounding: failed to fetch %s - %s", code, exc, exc_info=False
            )
            continue
        df = df_map.get(code)
        if df is None or df.empty:
            logger.info("grounding: no data returned for %s", code)
            continue
        rows: list[dict] = []
        for ts, row in df.iterrows():
            rows.append({
                "trade_date": getattr(ts, "isoformat", lambda: str(ts))(),
                "open": float(row.get("open", 0.0)),
                "high": float(row.get("high", 0.0)),
                "low": float(row.get("low", 0.0)),
                "close": float(row.get("close", 0.0)),
                "volume": float(row.get("volume", 0.0)),
            })
        if rows:
            out[code] = rows
    return out


def fetch_multitimeframe_grounding_data(
    symbols: Iterable[str],
    *,
    timeframe_windows: Mapping[str, int] | None = None,
    now: datetime | None = None,
) -> dict[str, list[dict]]:
    """Fetch timestamped, source-labelled grounding bars for TradeCoreFX.

    Forex symbols receive 1D, 4H, and 1H data by default. Other markets stay
    on the legacy 1D window until their loaders are validated for intraday use.
    The returned shape remains ``dict[str, list[dict]]`` so persisted
    ``SwarmRun.grounding_data`` records remain backward compatible.

    Args:
        symbols: Project symbols such as ``EUR/USD`` or ``NVDA.US``.
        timeframe_windows: Optional timeframe-to-calendar-days override.
        now: Capture time override used by deterministic tests.

    Returns:
        Original symbols mapped to bars. Each new-format bar includes
        ``timeframe``, ``source``, ``fetched_at``, ``market``, and ``is_stale``
        in addition to the legacy OHLCV fields.
    """
    symbols_list = list(symbols)
    if not symbols_list:
        return {}

    from backtest.runner import _detect_market

    captured_at = _as_utc(now or datetime.now(timezone.utc))
    fetched_at = captured_at.isoformat()
    requested_windows = dict(timeframe_windows or DEFAULT_FX_TIMEFRAME_WINDOWS)
    out: dict[str, list[dict]] = {}

    for code in symbols_list:
        try:
            market = _detect_market(code)
        except Exception as exc:
            logger.warning(
                "grounding: failed to detect market for %s - %s",
                code,
                exc,
                exc_info=False,
            )
            continue

        windows = (
            requested_windows
            if market == "forex"
            else {"1D": DEFAULT_WINDOW_DAYS}
        )
        symbol_rows: list[dict] = []

        for timeframe, window_days in windows.items():
            end = captured_at.date()
            start = end - timedelta(days=max(1, int(window_days)))
            try:
                loader = _resolve_grounding_loader(market, timeframe)
                source = str(getattr(loader, "name", loader.__class__.__name__))
                df_map = loader.fetch(
                    [code],
                    start.isoformat(),
                    end.isoformat(),
                    interval=timeframe,
                )
            except Exception as exc:  # pragma: no cover - depends on network
                logger.warning(
                    "grounding: failed to fetch %s timeframe=%s - %s",
                    code,
                    timeframe,
                    exc,
                    exc_info=False,
                )
                continue

            frame = df_map.get(code)
            if frame is None or frame.empty:
                logger.info(
                    "grounding: no data returned for %s timeframe=%s",
                    code,
                    timeframe,
                )
                continue

            timeframe_rows: list[dict] = []
            for ts, row in frame.iterrows():
                timeframe_rows.append({
                    "trade_date": getattr(ts, "isoformat", lambda: str(ts))(),
                    "open": float(row.get("open", 0.0)),
                    "high": float(row.get("high", 0.0)),
                    "low": float(row.get("low", 0.0)),
                    "close": float(row.get("close", 0.0)),
                    "volume": float(row.get("volume", 0.0)),
                    "timeframe": timeframe,
                    "source": source,
                    "fetched_at": fetched_at,
                    "market": market,
                })

            if not timeframe_rows:
                continue

            stale = _is_stale_bar(
                timeframe_rows[-1]["trade_date"],
                timeframe,
                captured_at,
                market,
            )
            for row in timeframe_rows:
                row["is_stale"] = stale
            symbol_rows.extend(timeframe_rows)

        if symbol_rows:
            out[code] = symbol_rows

    return out


def _has_multitimeframe_metadata(grounding: dict[str, list[dict]]) -> bool:
    """Return whether any grounding row uses the new metadata contract."""
    return any(
        "timeframe" in row
        for rows in grounding.values()
        for row in rows
    )


def _format_metric(value: object, decimals: int = 2) -> str:
    """Format an optional finite metric without inventing a value."""
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{number:.{decimals}f}"


def _format_feature_evidence(
    snapshots: dict[str, dict[str, object]],
    ordered_timeframes: list[str],
    price_decimals: int,
) -> list[str]:
    """Render deterministic indicators for one symbol."""
    lines = [
        "",
        "#### Deterministic indicator evidence",
        "",
        "Indicators below are computed from the complete bar windows, not "
        "estimated by the model.",
        "",
        "| TF | History | MA trend | Momentum | RSI14 | MACD hist | ADX/+DI/-DI | ATR14 (% close) | Return 5/20 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    volume_unavailable = False

    for timeframe in ordered_timeframes:
        snapshot = snapshots.get(timeframe, {})
        trend = snapshot.get("trend", {})
        momentum = snapshot.get("momentum", {})
        directional = snapshot.get("directional", {})
        volatility = snapshot.get("volatility", {})
        returns = snapshot.get("returns_pct", {})

        if not snapshot.get("volume_available", False):
            volume_unavailable = True

        adx_text = "/".join(
            (
                _format_metric(directional.get("adx14")),
                _format_metric(directional.get("plus_di14")),
                _format_metric(directional.get("minus_di14")),
            )
        )
        atr_text = (
            f"{_format_metric(volatility.get('atr14'), price_decimals)} "
            f"({_format_metric(volatility.get('atr_percent'), 3)}%)"
        )
        return_text = (
            f"{_format_metric(returns.get('5_bar'), 3)}% / "
            f"{_format_metric(returns.get('20_bar'), 3)}%"
        )
        lines.append(
            f"| {timeframe} | {snapshot.get('status', 'unavailable')} | "
            f"{trend.get('alignment', 'unavailable')} | "
            f"{momentum.get('alignment', 'unavailable')} | "
            f"{_format_metric(momentum.get('rsi14'))} | "
            f"{_format_metric(momentum.get('macd_histogram'), price_decimals)} | "
            f"{adx_text} | {atr_text} | {return_text} |"
        )

    lines.extend([
        "",
        "| TF | SMA20 | SMA50 | SMA200 | Bollinger lower-upper | 20-bar low-high | 50-bar low-high |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for timeframe in ordered_timeframes:
        snapshot = snapshots.get(timeframe, {})
        trend = snapshot.get("trend", {})
        volatility = snapshot.get("volatility", {})
        levels = snapshot.get("levels", {})
        lines.append(
            f"| {timeframe} | "
            f"{_format_metric(trend.get('sma20'), price_decimals)} | "
            f"{_format_metric(trend.get('sma50'), price_decimals)} | "
            f"{_format_metric(trend.get('sma200'), price_decimals)} | "
            f"{_format_metric(volatility.get('bb_lower'), price_decimals)} - "
            f"{_format_metric(volatility.get('bb_upper'), price_decimals)} | "
            f"{_format_metric(levels.get('low_20'), price_decimals)} - "
            f"{_format_metric(levels.get('high_20'), price_decimals)} | "
            f"{_format_metric(levels.get('low_50'), price_decimals)} - "
            f"{_format_metric(levels.get('high_50'), price_decimals)} |"
        )

    if volume_unavailable:
        lines.extend([
            "",
            "**Volume discipline:** This public spot-FX feed has no reliable "
            "centralized volume. Do not claim OBV, volume confirmation, or "
            "institutional order flow from zero-filled volume bars.",
        ])
    return lines


def _format_multitimeframe_grounding_block(
    grounding: dict[str, list[dict]],
) -> str:
    """Render source-labelled multi-timeframe bars for worker prompts."""
    from src.swarm.forex_features import build_multitimeframe_snapshots

    sections: list[str] = []
    captured_values: list[str] = []
    timeframe_rank = {name: index for index, name in enumerate(("1D", "4H", "1H"))}
    feature_snapshots = build_multitimeframe_snapshots(grounding)

    for code, rows in grounding.items():
        enriched = [row for row in rows if row.get("timeframe")]
        if not enriched:
            continue

        grouped: dict[str, list[dict]] = {}
        for row in enriched:
            grouped.setdefault(str(row["timeframe"]), []).append(row)
            if row.get("fetched_at"):
                captured_values.append(str(row["fetched_at"]))

        decimals = _price_decimals(code)
        lines = [f"### {code}", ""]
        lines.extend([
            "| Timeframe | Source | Latest bar | Freshness | Latest close | Observed low-high | Bars |",
            "| --- | --- | --- | --- | ---: | ---: | ---: |",
        ])

        ordered_timeframes = sorted(
            grouped,
            key=lambda value: (timeframe_rank.get(value, 99), value),
        )
        for timeframe in ordered_timeframes:
            timeframe_rows = sorted(grouped[timeframe], key=lambda row: row["trade_date"])
            latest = timeframe_rows[-1]
            observed_low = min(float(row["low"]) for row in timeframe_rows)
            observed_high = max(float(row["high"]) for row in timeframe_rows)
            freshness = "STALE" if latest.get("is_stale") else "FRESH"
            lines.append(
                f"| {timeframe} | {latest.get('source', 'unknown')} | "
                f"{str(latest['trade_date']).replace('T', ' ')} | {freshness} | "
                f"{float(latest['close']):.{decimals}f} | "
                f"{observed_low:.{decimals}f} - {observed_high:.{decimals}f} | "
                f"{len(timeframe_rows)} |"
            )

        lines.extend(
            _format_feature_evidence(
                feature_snapshots.get(code, {}),
                ordered_timeframes,
                decimals,
            )
        )

        for timeframe in ordered_timeframes:
            timeframe_rows = sorted(grouped[timeframe], key=lambda row: row["trade_date"])
            lines.extend([
                "",
                f"#### {timeframe} recent bars",
                "",
                "| Bar timestamp | Open | High | Low | Close |",
                "| --- | ---: | ---: | ---: | ---: |",
            ])
            for row in timeframe_rows[-MULTITIMEFRAME_PROMPT_TAIL:]:
                lines.append(
                    f"| {str(row['trade_date']).replace('T', ' ')} | "
                    f"{float(row['open']):.{decimals}f} | "
                    f"{float(row['high']):.{decimals}f} | "
                    f"{float(row['low']):.{decimals}f} | "
                    f"{float(row['close']):.{decimals}f} |"
                )

        sections.append("\n".join(lines))

    if not sections:
        return ""

    captured_at = max(captured_values) if captured_values else "unknown"
    header = (
        "## Ground Truth \u2014 Multi-Timeframe Market Data\n\n"
        f"**Captured:** {captured_at}. "
        "These are historical public-source bars, not broker-executable quotes. "
        "Treat only rows marked **FRESH** as current context. A **STALE** row "
        "must be refreshed before making a current-price claim. Use the stated "
        "source, timeframe, and bar timestamp when citing a value. Do NOT invent "
        "missing prices or silently substitute training-data knowledge."
    )
    return header + "\n\n" + "\n\n".join(sections)


def format_grounding_block(grounding: dict[str, list[dict]]) -> str:
    """Render *grounding* as a markdown block ready to splice into a prompt.

    Returns the empty string if no symbol has any data - callers can use
    that as a falsy guard so the section is omitted entirely instead of
    rendering an empty heading.
    """
    if not grounding:
        return ""
    if _has_multitimeframe_metadata(grounding):
        return _format_multitimeframe_grounding_block(grounding)

    sections: list[str] = []
    for code, rows in grounding.items():
        if not rows:
            continue
        price_decimals = _price_decimals(code)
        first_date = rows[0]["trade_date"][:10]
        last_date = rows[-1]["trade_date"][:10]
        closes = [row["close"] for row in rows]
        window_low = min(closes)
        window_high = max(closes)
        last_close = closes[-1]

        lines = [
            f"### {code}  (window {first_date} \u2192 {last_date})",
            "",
            "| Date | Close | Volume |",
            "| --- | ---: | ---: |",
        ]
        for row in rows[-PROMPT_TABLE_TAIL:]:
            lines.append(
                f"| {row['trade_date'][:10]} | "
                f"{row['close']:.{price_decimals}f} "
                f"| {int(row['volume']):,} |"
            )
        lines.append("")
        lines.append(
            f"**Latest close:** {last_close:.{price_decimals}f} "
            f"({last_date})  **Window range:** "
            f"{window_low:.{price_decimals}f} \u2013 "
            f"{window_high:.{price_decimals}f}"
        )
        sections.append("\n".join(lines))

    if not sections:
        return ""

    header = (
        "## Ground Truth \u2014 Recent Market Data\n\n"
        "**These are the authoritative current prices for this run.** Do NOT "
        "cite prices, valuations, multiples, or returns from your training "
        "data \u2014 markets have moved. If you need a price outside this window, "
        "call `get_market_data` for the relevant range. When you state a "
        "price, cite the date from this table."
    )
    return header + "\n\n" + "\n\n".join(sections)
