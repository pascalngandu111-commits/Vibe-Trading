"""Deterministic validation contracts for the TradeCoreFX beta.

This module does not predict prices and does not place orders.  It turns an
injected OHLCV fixture (or captured provider response) into a machine-readable
evidence report and audits structured numerical claims against that evidence.
"""

from __future__ import annotations

import math
import re
import json
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence
from urllib.parse import urlparse

from src.swarm.forex_features import build_multitimeframe_snapshots


REQUIRED_TIMEFRAMES = ("1D", "4H", "1H")
DECISIONS = {"LONG_SETUP", "SHORT_SETUP", "WAIT", "NO_TRADE_DATA"}
CONFIDENCE_CAPS = {
    "data": 25,
    "alignment": 25,
    "timing": 15,
    "momentum": 15,
    "macro": 10,
    "risk_reward": 10,
}
_PAIR = re.compile(r"^(?P<base>[A-Z]{3})(?:/)?(?P<quote>[A-Z]{3})(?:\.FX)?$")
INVALID_DIRECTION_REASON = "INVALID_DIRECTION: direction must be LONG or SHORT"
NON_FINITE_LEVEL_REASON = "NON_FINITE_LEVEL: entry, stop, and target must be finite"
LEVEL_GROUNDING_REASON = "LEVEL_GROUNDING_FAILED: entry, stop, and target require matching evidence references"
LATEST_BAR_INVALID_REASON = "LATEST_BAR_INVALID: chronologically newest raw row failed OHLC validation"
BAR_ROW_INVALID_REASON = (
    "BAR_ROW_INVALID: raw row failed timeframe, timestamp, or OHLC validation; "
    "LATEST_BAR_INVALID if it is the newest parseable row"
)
MIXED_DATA_PROVIDERS_REASON = (
    "MIXED_DATA_PROVIDERS: valid bars contain more than one provider source"
)
MACRO_MAX_AGE = timedelta(hours=24)
REPORT_SCHEMA_VERSION = "tradecorefx.beta.batch1.v1"
AUDIT_SCHEMA_VERSION = "tradecorefx.audit.batch1.v1"
EVIDENCE_LABELS = {"SIMULATED", "CAPTURED_FIXTURE", "CAPTURED_PROVIDER"}
APPROVED_PUBLIC_PROVIDERS = {"yfinance"}
PROPOSED_LEVEL_FIELDS = {
    "direction", "entry", "stop", "target", "entry_ref", "stop_ref", "target_ref"
}
CLAIM_FIELDS = {"id", "kind", "value", "source", "observed_at", "evidence_ref"}
PRICE_CLAIM_FIELDS = {"open", "high", "low", "close"}
INDICATOR_LOCATIONS = {
    "sma20": ("trend", "sma20"),
    "sma50": ("trend", "sma50"),
    "sma200": ("trend", "sma200"),
    "ema12": ("trend", "ema12"),
    "ema26": ("trend", "ema26"),
    "rsi14": ("momentum", "rsi14"),
    "macd_line": ("momentum", "macd_line"),
    "macd_signal": ("momentum", "macd_signal"),
    "macd_histogram": ("momentum", "macd_histogram"),
    "atr14": ("volatility", "atr14"),
    "atr_percent": ("volatility", "atr_percent"),
    "bb_middle": ("volatility", "bb_middle"),
    "bb_upper": ("volatility", "bb_upper"),
    "bb_lower": ("volatility", "bb_lower"),
    "bb_width_percent": ("volatility", "bb_width_percent"),
    "adx14": ("directional", "adx14"),
    "plus_di14": ("directional", "plus_di14"),
    "minus_di14": ("directional", "minus_di14"),
}
MACRO_FIELDS = {"status", "claim", "source", "observed_at", "url"}
RECHECK_CONDITION = "Refresh failed evidence and re-run all binding gates"
REPORT_FIELDS = {
    "schema_version", "evidence_label", "pair", "timeframes", "run_id",
    "data_provider", "capture_time", "bar_evidence", "indicator_evidence",
    "agent_conclusions", "dissent", "binding_data_gate", "binding_risk_gate",
    "confidence", "macro", "proposed_levels", "market_claims",
    "final_decision", "recheck_condition", "disclosure", "audit",
}


def _disclosure(label: str) -> str:
    return (
        "Decision support only; no broker execution or guarantee. "
        f"Evidence is labelled {label}."
    )


def _evidence_label(value: object, bars: Mapping[str, object], *, trusted_provider_capture: bool = False) -> str:
    """Conservatively label deterministic fixtures as simulated evidence."""
    requested = value if isinstance(value, str) and value in EVIDENCE_LABELS else "SIMULATED"
    sources = [
        item.get("source")
        for item in bars.values()
        if isinstance(item, Mapping) and isinstance(item.get("source"), str)
    ]
    if any(source.strip().lower() == "deterministic-fixture" for source in sources):
        return "SIMULATED"
    if requested == "CAPTURED_PROVIDER":
        normalized = {source.strip().lower() for source in sources}
        if not trusted_provider_capture or len(normalized) != 1 or not normalized <= APPROVED_PUBLIC_PROVIDERS:
            return "SIMULATED"
    return requested


def _json_safe_string(value: object, fallback: str) -> str:
    """Return a non-empty string that strict JSON can always encode."""
    if isinstance(value, str) and value.strip():
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, OverflowError):
            pass
        else:
            return value
    return fallback


def normalize_forex_pair(value: object) -> str | None:
    """Return canonical ``AAA/BBB`` syntax, or ``None`` for malformed input."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if "/" not in candidate and not candidate.endswith(".FX"):
        return None
    match = _PAIR.fullmatch(candidate)
    if match is None or match["base"] == match["quote"]:
        return None
    return f"{match['base']}/{match['quote']}"


def price_decimals(pair: str) -> int:
    """Return three decimals for JPY quotes and five for other FX pairs."""
    normalized = normalize_forex_pair(pair)
    if normalized is None:
        raise ValueError(f"Unsupported or malformed forex pair: {pair!r}")
    return 3 if normalized.endswith("/JPY") else 5


def format_fx_price(pair: str, value: float) -> str:
    """Format a finite FX price without silently accepting invalid values."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("FX prices must be positive finite numbers") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("FX prices must be positive finite numbers")
    return f"{number:.{price_decimals(pair)}f}"


def _iso_utc(value: object) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _pair_rows(
    pair: object,
    grounding: object,
) -> tuple[str | None, list[object]]:
    """Select exactly one canonical pair, preventing cross-pair evidence leaks."""
    normalized = normalize_forex_pair(pair)
    if normalized is None:
        return None, []
    if not isinstance(grounding, Mapping):
        return normalized, []
    matches = [
        rows
        for symbol, rows in grounding.items()
        if normalize_forex_pair(symbol) == normalized
    ]
    if (
        len(matches) != 1
        or not isinstance(matches[0], Sequence)
        or isinstance(matches[0], (str, bytes))
    ):
        return normalized, []
    # Retain every entry in the selected pair's collection.  Non-object entries
    # are rejected downstream rather than disappearing before the data gate.
    return normalized, [dict(row) if isinstance(row, Mapping) else row for row in matches[0]]


def _valid_ohlc_rows(rows: list[object], timeframe: str) -> tuple[list[dict], bool]:
    """Return normalized valid rows and whether any matching raw row was rejected."""
    candidates: list[tuple[datetime, int, dict, bool]] = []
    rejected = False
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        if row.get("timeframe") != timeframe:
            continue
        timestamp = _iso_utc(row.get("trade_date"))
        values = [_finite_number(row.get(name)) for name in ("open", "high", "low", "close")]
        valid = bool(
            timestamp
            and all(value is not None and value > 0 for value in values)
            and values[1] >= max(values[0], values[3])
            and values[2] <= min(values[0], values[3])
            and values[1] >= values[2]
        )
        if timestamp:
            parsed = datetime.fromisoformat(timestamp)
            candidates.append((parsed, index, row, valid))
        if not valid:
            rejected = True
    if not candidates:
        return [], bool(any(
            isinstance(row, Mapping) and row.get("timeframe") == timeframe
            for row in rows
        ))

    by_timestamp: dict[datetime, tuple[int, dict]] = {}
    for parsed, index, row, valid in candidates:
        if valid:
            by_timestamp[parsed] = (index, row)
    normalized = [by_timestamp[key][1] for key in sorted(by_timestamp)]
    return normalized, rejected


def _bar_contract(rows: list[object]) -> tuple[dict[str, dict], dict[str, list[dict]], set[str]]:
    result: dict[str, dict] = {}
    valid_by_timeframe: dict[str, list[dict]] = {}
    latest_invalid: set[str] = set()
    for timeframe in REQUIRED_TIMEFRAMES:
        selected, newest_raw_invalid = _valid_ohlc_rows(rows, timeframe)
        valid_by_timeframe[timeframe] = selected
        if newest_raw_invalid:
            latest_invalid.add(timeframe)
        if not selected:
            result[timeframe] = {
                "source": None,
                "captured_at": None,
                "last_bar_at": None,
                "freshness": "MISSING",
                "history_status": "insufficient_data",
                "bars": 0,
            }
            continue
        latest = selected[-1]
        result[timeframe] = {
        "source": (
            latest.get("source")
            if isinstance(latest.get("source"), str) and latest.get("source").strip()
            else None
        ),
            "captured_at": _iso_utc(latest.get("fetched_at")),
            "last_bar_at": _iso_utc(latest.get("trade_date")),
            "freshness": "STALE" if latest.get("is_stale") is True else "FRESH",
            "history_status": None,
            "bars": len(selected),
        }
    return result, valid_by_timeframe, latest_invalid


def _provider_provenance(
    valid_by_timeframe: Mapping[str, list[dict]],
) -> tuple[str | None, set[str], bool]:
    """Derive complete-history provider provenance for all contributing bars."""
    sources: set[str] = set()
    missing: set[str] = set()
    for timeframe in REQUIRED_TIMEFRAMES:
        for row in valid_by_timeframe.get(timeframe, []):
            source = row.get("source")
            if isinstance(source, str) and source.strip():
                sources.add(source)
            else:
                missing.add(timeframe)
    mixed = len(sources) > 1
    return (next(iter(sources)) if len(sources) == 1 else None), missing, mixed


def _confidence(data_pass: bool, conflict: bool, macro_verified: bool, risk_pass: bool) -> dict:
    components = {
        "data": 25 if data_pass else 0,
        "alignment": 0 if conflict else (20 if data_pass else 0),
        "timing": 8 if data_pass else 0,
        "momentum": 8 if data_pass else 0,
        "macro": 10 if macro_verified else 0,
        "risk_reward": 10 if risk_pass else 0,
    }
    if not macro_verified:
        remaining = 50
        for name, value in components.items():
            components[name] = min(value, remaining)
            remaining -= components[name]
    total = sum(components.values())
    return {"components": components, "total": total, "cap": 100 if macro_verified else 50}


def _macro_verified(macro: object, capture_time: object) -> bool:
    """Return whether macro evidence satisfies the deterministic contract."""
    if not isinstance(macro, Mapping):
        return False
    if set(macro) != {"claim", "source", "observed_at", "url"} and set(macro) != MACRO_FIELDS:
        return False
    if "status" in macro and macro.get("status") != "VERIFIED":
        return False
    claim = macro.get("claim")
    source = macro.get("source")
    observed_at = macro.get("observed_at")
    url = macro.get("url")
    if not all(isinstance(value, str) and value.strip() for value in (claim, source, observed_at, url)):
        return False
    try:
        parsed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        parsed_capture = datetime.fromisoformat(str(capture_time).replace("Z", "+00:00"))
        parsed_url = urlparse(url)
        if (
            parsed_at.tzinfo is None
            or parsed_at.utcoffset() is None
            or parsed_capture.tzinfo is None
            or parsed_capture.utcoffset() is None
        ):
            return False
        observed_utc = parsed_at.astimezone(timezone.utc)
        capture_utc = parsed_capture.astimezone(timezone.utc)
        age = capture_utc - observed_utc
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return (
        timedelta(0) <= age <= MACRO_MAX_AGE
        and parsed_url.scheme == "https"
        and bool(parsed_url.hostname)
    )


def _agent_conclusions(
    data_pass: bool, conflict: bool, risk_pass: bool, macro_verified: bool
) -> dict[str, dict]:
    data_state = "PASS" if data_pass else "FAIL"
    return {
        "fx_structure_analyst": {
            "data_gate": data_state,
            "conclusion": "conflicting" if conflict else "evidence recorded",
        },
        "fx_momentum_regime_analyst": {
            "data_gate": data_state,
            "conclusion": "deterministic indicators recorded",
        },
        "fx_macro_catalyst_analyst": {
            "current_macro": "VERIFIED" if macro_verified else "UNAVAILABLE",
            "conclusion": (
                "Verified current macro source was injected"
                if macro_verified
                else "No verified current macro source was injected"
            ),
        },
        "fx_risk_gatekeeper": {
            "risk_gate": "PASS" if risk_pass else "FAIL",
            "conclusion": "binding gate",
        },
        "chief_fx_strategist": {
            "conclusion": "safe-state decision applied",
        },
    }


def _finite_number(value: object) -> float | None:
    """Return a finite real number, excluding booleans, or ``None``."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _ground_levels(
    levels: Mapping[str, object], snapshots: Mapping[str, object]
) -> tuple[dict[str, object], list[str]]:
    """Sanitize and bind proposed levels to this pair's calculated evidence."""
    sanitized: dict[str, object] = {}
    reasons: list[str] = []
    raw_direction = levels.get("direction")
    sanitized["direction"] = (
        raw_direction.strip().upper() if isinstance(raw_direction, str) else None
    )
    supplied: dict[str, float] = {}
    for name in ("entry", "stop", "target"):
        number = _finite_number(levels.get(name))
        if number is None:
            if name in levels and isinstance(levels.get(name), (int, float)):
                reasons.append(NON_FINITE_LEVEL_REASON)
            else:
                reasons.append(LEVEL_GROUNDING_REASON)
            sanitized[name] = None
        reference = levels.get(f"{name}_ref")
        sanitized[f"{name}_ref"] = reference if isinstance(reference, str) else None
        if number is None:
            continue
        supplied[name] = number
        sanitized[name] = number
        if not isinstance(reference, str) or not reference:
            reasons.append(LEVEL_GROUNDING_REASON)
            continue
        expected = _finite_number(_resolve_ref(snapshots, reference))
        if expected is None or not math.isclose(number, expected, rel_tol=1e-9, abs_tol=1e-9):
            reasons.append(LEVEL_GROUNDING_REASON)
    return sanitized, list(dict.fromkeys(reasons))


def _derive_data_gate(
    normalized: str | None, rows: list[object]
) -> tuple[dict[str, dict], dict[str, object], list[str], bool, str | None]:
    """Derive the binding data gate solely from isolated grounding rows."""
    bars, valid_by_timeframe, latest_invalid = _bar_contract(rows)
    data_provider, missing_sources, mixed_providers = _provider_provenance(
        valid_by_timeframe
    )
    valid_rows = [row for timeframe in REQUIRED_TIMEFRAMES for row in valid_by_timeframe[timeframe]]
    try:
        snapshots = build_multitimeframe_snapshots(
            {normalized: valid_rows} if normalized and valid_rows else {}
        ).get(normalized or "", {})
    except (KeyError, TypeError, ValueError, OverflowError):
        snapshots = {}
    # Expose one closed, flat namespace for numerical indicator claims.  The
    # analytical groupings remain available, but are not claim-reference APIs.
    for snapshot in snapshots.values():
        if isinstance(snapshot, dict):
            snapshot["indicators"] = {
                name: _resolve_ref(snapshot, ".".join(location))
                for name, location in INDICATOR_LOCATIONS.items()
            }
    reasons: list[str] = []
    for timeframe in REQUIRED_TIMEFRAMES:
        snapshot = snapshots.get(timeframe, {})
        bars[timeframe]["history_status"] = snapshot.get(
            "status", "insufficient_data"
        )
        if bars[timeframe]["freshness"] == "MISSING":
            reasons.append(f"{timeframe}: missing required timeframe")
        elif bars[timeframe]["freshness"] == "STALE":
            reasons.append(f"{timeframe}: stale data")
        if snapshot.get("status") != "ok":
            reasons.append(
                f"{timeframe}: history {snapshot.get('status', 'insufficient_data')}"
            )
        if timeframe in missing_sources or not bars[timeframe].get("source"):
            reasons.append(f"{timeframe}: missing source attribution")
        if not bars[timeframe].get("captured_at") or not bars[timeframe].get("last_bar_at"):
            reasons.append(f"{timeframe}: missing timestamp metadata")
        if timeframe in latest_invalid:
            reasons.append(f"{timeframe}: {BAR_ROW_INVALID_REASON}")
    for index, row in enumerate(rows):
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("timeframe"), str)
            or row.get("timeframe") not in REQUIRED_TIMEFRAMES
        ):
            reasons.append(f"selected pair row {index}: {BAR_ROW_INVALID_REASON}")
    if mixed_providers:
        reasons.append(MIXED_DATA_PROVIDERS_REASON)
    if normalized is None:
        reasons.insert(0, "unsupported or malformed pair")
    elif not rows:
        reasons.insert(0, "provider returned no isolated pair evidence")
    daily = snapshots.get("1D", {}).get("trend", {}).get("alignment")
    four_hour = snapshots.get("4H", {}).get("trend", {}).get("alignment")
    return (
        bars,
        snapshots,
        reasons,
        {daily, four_hour} == {"bullish", "bearish"},
        data_provider,
    )


def _derive_risk_gate(
    levels: object,
    snapshots: Mapping[str, object],
    *,
    data_pass: bool,
    conflict: bool,
    macro_verified: bool,
) -> tuple[dict[str, object] | None, list[str], float | None, str | None]:
    """Derive the binding risk gate without trusting a reported gate object."""
    reasons: list[str] = []
    reward_to_risk: float | None = None
    sanitized = dict(levels) if isinstance(levels, Mapping) else {}
    direction: str | None = None
    if sanitized:
        sanitized, grounding_reasons = _ground_levels(sanitized, snapshots)
        reasons.extend(grounding_reasons)
        candidate = str(sanitized.get("direction", "")).strip().upper()
        if candidate not in {"LONG", "SHORT"}:
            reasons.append(INVALID_DIRECTION_REASON)
        else:
            direction = candidate
            if not grounding_reasons:
                entry, stop, target = (
                    sanitized[name] for name in ("entry", "stop", "target")
                )
                risk, reward = (
                    (entry - stop, target - entry)
                    if direction == "LONG"
                    else (stop - entry, entry - target)
                )
                if risk <= 0 or reward <= 0:
                    reasons.append("entry/stop/target geometry is invalid")
                else:
                    reward_to_risk = reward / risk
                    if not all(math.isfinite(value) for value in (risk, reward, reward_to_risk)):
                        reasons.append(NON_FINITE_LEVEL_REASON)
                        reward_to_risk = None
                    elif reward_to_risk < 2.0:
                        reasons.append("reward-to-risk below 2.0")
            expected_alignment = "bullish" if direction == "LONG" else "bearish"
            alignments = {
                snapshots.get(timeframe, {}).get("trend", {}).get("alignment")
                for timeframe in ("1D", "4H")
            }
            if any(value in {"bullish", "bearish"} and value != expected_alignment for value in alignments):
                reasons.append("proposed direction conflicts with 1D/4H alignment")
    else:
        reasons.append("no grounded conditional entry/stop/target plan")
    if not data_pass:
        reasons.append("binding data gate failed")
    if conflict:
        reasons.append("1D/4H directional conflict")
    if not macro_verified:
        reasons.append("verified current macro support unavailable")
    return sanitized or None, list(dict.fromkeys(reasons)), reward_to_risk, direction


def _build_validation_report(
    pair: object,
    grounding: object,
    *,
    run_id: object,
    evidence_label: object = "SIMULATED",
    provider: object = None,
    macro_evidence: object = None,
    proposed_levels: object = None,
    trusted_provider_capture: bool = False,
) -> dict:
    """Build the complete local-safe report contract from injected evidence.

    ``evidence_label`` must accurately describe the caller's input, normally
    ``SIMULATED`` or ``CAPTURED_FIXTURE`` in deterministic tests.
    """
    normalized, rows = _pair_rows(pair, grounding)
    bars, snapshots, data_reasons, conflict, data_provider = _derive_data_gate(
        normalized, rows
    )
    data_pass = not data_reasons

    capture_times = [item["captured_at"] for item in bars.values() if item["captured_at"]]
    capture_time = max(capture_times) if capture_times else None
    safe_label = _evidence_label(evidence_label, bars, trusted_provider_capture=trusted_provider_capture)
    if safe_label == "CAPTURED_PROVIDER" and (
        not data_pass
        or set(bars) != set(REQUIRED_TIMEFRAMES)
        or not isinstance(data_provider, str)
        or data_provider.strip().lower() not in APPROVED_PUBLIC_PROVIDERS
    ):
        safe_label = "SIMULATED"
    safe_run_id = _json_safe_string(run_id, "invalid-run-id")
    macro = dict(macro_evidence) if isinstance(macro_evidence, Mapping) else {}
    macro_verified = _macro_verified(macro, capture_time)
    macro_contract = (
        {
            "status": "VERIFIED",
            "claim": macro["claim"],
            "source": macro["source"],
            "observed_at": macro["observed_at"],
            "url": macro["url"],
        }
        if macro_verified
        else {
            "status": "UNAVAILABLE",
            "claim": None,
            "source": None,
            "observed_at": None,
            "url": None,
        }
    )

    levels, risk_reasons, reward_to_risk, direction = _derive_risk_gate(
        proposed_levels, snapshots, data_pass=data_pass, conflict=conflict,
        macro_verified=macro_verified,
    )
    risk_pass = not risk_reasons

    if not data_pass:
        decision = "NO_TRADE_DATA"
    elif not risk_pass:
        decision = "WAIT"
    else:
        decision = "LONG_SETUP" if direction == "LONG" else "SHORT_SETUP"

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evidence_label": safe_label,
        "pair": normalized or "INVALID/PAIR",
        "timeframes": list(REQUIRED_TIMEFRAMES),
        "run_id": safe_run_id,
        "data_provider": data_provider,
        "capture_time": capture_time,
        "bar_evidence": bars,
        "indicator_evidence": snapshots,
        "agent_conclusions": _agent_conclusions(
            data_pass, conflict, risk_pass, macro_verified
        ),
        "dissent": ["1D and 4H directional evidence conflicts"] if conflict else [],
        "binding_data_gate": {"status": "PASS" if data_pass else "FAIL", "reasons": data_reasons},
        "binding_risk_gate": {
            "status": "PASS" if risk_pass else "FAIL",
            "reasons": risk_reasons,
            "reward_to_risk": reward_to_risk,
        },
        "confidence": _confidence(data_pass, conflict, macro_verified, risk_pass),
        "macro": macro_contract,
        "proposed_levels": levels or None,
        "market_claims": [],
        "final_decision": decision,
        "recheck_condition": RECHECK_CONDITION,
        "disclosure": _disclosure(safe_label),
        "audit": {},
    }
    report["audit"] = _safe_audit_validation_report(
        report,
        {normalized: rows} if normalized else {},
        trusted_provider_capture=trusted_provider_capture,
    )
    return report


def _resolve_ref(snapshots: Mapping[str, object], reference: str) -> object:
    current: object = snapshots
    for part in reference.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _issue(code: str, message: str, claim_id: object = None) -> dict:
    safe_claim_id = claim_id if isinstance(claim_id, (str, int)) and not isinstance(claim_id, bool) else None
    if isinstance(safe_claim_id, int) and _finite_number(safe_claim_id) is None:
        safe_claim_id = None
    return {"code": code, "severity": "error", "claim_id": safe_claim_id, "message": message}


def _strict_json_equal(reported: object, derived: object) -> bool:
    """Compare complete JSON values while rejecting non-finite numbers."""
    if isinstance(derived, Mapping):
        return (
            isinstance(reported, dict)
            and set(reported) == set(derived)
            and all(_strict_json_equal(reported[key], value) for key, value in derived.items())
        )
    if isinstance(derived, list):
        return (
            isinstance(reported, list)
            and len(reported) == len(derived)
            and all(_strict_json_equal(left, right) for left, right in zip(reported, derived))
        )
    if isinstance(derived, bool) or derived is None or isinstance(derived, str):
        return type(reported) is type(derived) and reported == derived
    if isinstance(derived, (int, float)):
        reported_number = (
            _finite_number(reported)
            if isinstance(reported, (int, float)) and not isinstance(reported, bool)
            else None
        )
        derived_number = _finite_number(derived)
        return reported_number is not None and derived_number is not None and reported_number == derived_number
    return False


def _evidence_bar(rows: list[object], reference: str) -> dict | None:
    """Return the injected latest bar for the timeframe named by a reference."""
    timeframe, separator, _ = reference.partition(".")
    if not separator or timeframe not in REQUIRED_TIMEFRAMES:
        return None
    selected, _ = _valid_ohlc_rows(rows, timeframe)
    return selected[-1] if selected else None


def _claim_reference_matches_kind(kind: str, reference: object) -> bool:
    """Enforce the exact, closed numerical-claim reference grammar."""
    if not isinstance(reference, str):
        return False
    parts = reference.split(".")
    if len(parts) != 3 or parts[0] not in REQUIRED_TIMEFRAMES:
        return False
    if kind == "price":
        return parts[1] == "latest" and parts[2] in PRICE_CLAIM_FIELDS
    if kind == "indicator":
        return parts[1] == "indicators" and parts[2] in INDICATOR_LOCATIONS
    return False


def _audit_validation_report(
    report: object, grounding: object, *, trusted_provider_capture: bool = False
) -> dict:
    """Audit a structured report against injected bars and calculated features."""
    issues: list[dict] = []
    if not isinstance(report, Mapping):
        return {
            "schema_version": AUDIT_SCHEMA_VERSION, "passed": False, "issue_count": 1,
            "issues": [_issue("INVALID_REPORT_TYPE", "Report must be an object")],
        }
    if set(report) != REPORT_FIELDS:
        issues.append(_issue("REPORT_FIELDS_MISMATCH", "Report field set differs from the Batch 1 schema"))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        issues.append(_issue("SCHEMA_VERSION_MISMATCH", "Report schema_version is not the exact Batch 1 version"))
    label = report.get("evidence_label")
    if not isinstance(label, str) or label not in EVIDENCE_LABELS:
        issues.append(_issue("EVIDENCE_LABEL_INVALID", "Batch 1 evidence_label must be SIMULATED or CAPTURED_FIXTURE"))
    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        issues.append(_issue("RUN_ID_INVALID", "run_id must be a non-empty JSON-safe string"))
    if not _strict_json_equal(report.get("timeframes"), list(REQUIRED_TIMEFRAMES)):
        issues.append(_issue("TIMEFRAMES_MISMATCH", "Reported timeframes differ from the Batch 1 contract"))
    pair = normalize_forex_pair(report.get("pair"))
    if pair is None or report.get("pair") != pair:
        issues.append(_issue("PAIR_MISMATCH", "Reported pair must be a canonical isolated FX pair"))
    selected_pair, rows = _pair_rows(pair or "", grounding)
    if selected_pair != pair:
        issues.append(_issue("PAIR_EVIDENCE_MISMATCH", "Reported pair lacks exactly one matching grounding collection"))
    bars, snapshots, data_reasons, conflict, data_provider = _derive_data_gate(
        selected_pair, rows
    )
    capture_times = [item["captured_at"] for item in bars.values() if item["captured_at"]]
    capture_time = max(capture_times) if capture_times else None
    expected_label = _evidence_label(
        label, bars, trusted_provider_capture=trusted_provider_capture
    )
    if expected_label == "CAPTURED_PROVIDER" and (
        data_reasons
        or set(bars) != set(REQUIRED_TIMEFRAMES)
        or not isinstance(data_provider, str)
        or data_provider.strip().lower() not in APPROVED_PUBLIC_PROVIDERS
    ):
        expected_label = "SIMULATED"
    if label in EVIDENCE_LABELS and label != expected_label:
        issues.append(_issue(
            "EVIDENCE_LABEL_SOURCE_MISMATCH",
            "Deterministic fixture sources must be labelled SIMULATED",
        ))
    derived_data_status = "PASS" if not data_reasons else "FAIL"
    expected_dissent = ["1D and 4H directional evidence conflicts"] if conflict else []
    if not _strict_json_equal(report.get("dissent"), expected_dissent):
        issues.append(_issue("DISSENT_MISMATCH", "Reported dissent differs from independently derived conflict state"))

    if not _strict_json_equal(report.get("bar_evidence"), bars):
        issues.append(_issue("BAR_EVIDENCE_MISMATCH", "Reported bar evidence differs from independently derived evidence"))
    if not _strict_json_equal(report.get("indicator_evidence"), snapshots):
        issues.append(_issue("INDICATOR_EVIDENCE_MISMATCH", "Reported indicator evidence differs from independently derived evidence"))
    if not _strict_json_equal(report.get("capture_time"), capture_time):
        issues.append(_issue("CAPTURE_TIME_MISMATCH", "Reported capture time differs from independently derived bar evidence"))
    if not _strict_json_equal(report.get("data_provider"), data_provider):
        issues.append(_issue("DATA_PROVIDER_MISMATCH", "Reported data provider differs from independently derived bar evidence"))

    decision = report.get("final_decision")
    if not isinstance(decision, str) or decision not in DECISIONS:
        issues.append(_issue("INVALID_DECISION", "Decision is outside the allowed enum"))
    data_gate = report.get("binding_data_gate", {})
    risk_gate = report.get("binding_risk_gate", {})
    data_status = data_gate.get("status") if isinstance(data_gate, Mapping) else None
    risk_status = risk_gate.get("status") if isinstance(risk_gate, Mapping) else None
    if not isinstance(data_status, str) or data_status not in {"PASS", "FAIL"}:
        issues.append(_issue("INVALID_DATA_GATE_STATUS", "Data-gate status must be exactly PASS or FAIL"))
    if not isinstance(risk_status, str) or risk_status not in {"PASS", "FAIL"}:
        issues.append(_issue("INVALID_RISK_GATE_STATUS", "Risk-gate status must be exactly PASS or FAIL"))
    data_gate_reasons = data_gate.get("reasons") if isinstance(data_gate, Mapping) else None
    if not _strict_json_equal(data_gate_reasons, data_reasons):
        issues.append(_issue("DATA_GATE_REASONS_MISMATCH", "Reported data-gate reasons differ from independently derived reasons"))
    if data_status in ("PASS", "FAIL") and data_status != derived_data_status:
        issues.append(_issue("DATA_GATE_MISMATCH", "Reported data gate differs from independently derived state"))
    if derived_data_status == "FAIL" and decision != "NO_TRADE_DATA":
        issues.append(_issue("DATA_GATE_OVERRIDE", "A failed data gate must bind to NO_TRADE_DATA"))
    if decision in ("LONG_SETUP", "SHORT_SETUP") and derived_data_status != "PASS":
        issues.append(_issue("DATA_GATE_OVERRIDE", "A directional setup requires a passed data gate"))
    elif decision in ("LONG_SETUP", "SHORT_SETUP") and data_status != "PASS":
        issues.append(_issue("DATA_GATE_OVERRIDE", "A directional setup requires a reported passed data gate"))

    macro = report.get("macro", {})
    if isinstance(macro, Mapping) and macro.get("status") == "VERIFIED":
        if not _macro_verified(macro, capture_time):
            issues.append(_issue("UNVERIFIED_MACRO", "Current macro must be valid and observed within 24 hours at or before independently derived evidence capture"))
    elif decision in ("LONG_SETUP", "SHORT_SETUP"):
        issues.append(_issue("MACRO_CAP", "Directional setup lacks verified macro support"))

    macro_verified = bool(
        isinstance(macro, Mapping)
        and macro.get("status") == "VERIFIED"
        and _macro_verified(macro, capture_time)
    )
    expected_macro = (
        {
            "status": "VERIFIED", "claim": macro.get("claim"),
            "source": macro.get("source"), "observed_at": macro.get("observed_at"),
            "url": macro.get("url"),
        }
        if macro_verified
        else {"status": "UNAVAILABLE", "claim": None, "source": None, "observed_at": None, "url": None}
    )
    if not _strict_json_equal(macro, expected_macro):
        issues.append(_issue("MACRO_CONTRACT_MISMATCH", "Macro field set or state differs from the exact Batch 1 contract"))
    _, risk_reasons, derived_reward_to_risk, derived_direction = _derive_risk_gate(
        report.get("proposed_levels") if isinstance(report.get("proposed_levels"), Mapping) else None,
        snapshots,
        data_pass=derived_data_status == "PASS",
        conflict=conflict,
        macro_verified=macro_verified,
    )
    derived_risk_status = "PASS" if not risk_reasons else "FAIL"
    if not _strict_json_equal(
        data_gate, {"status": derived_data_status, "reasons": data_reasons}
    ):
        issues.append(_issue("DATA_GATE_CONTRACT_MISMATCH", "Binding data gate differs from the complete derived contract"))
    if not _strict_json_equal(risk_gate, {
        "status": derived_risk_status, "reasons": risk_reasons,
        "reward_to_risk": derived_reward_to_risk,
    }):
        issues.append(_issue("RISK_GATE_CONTRACT_MISMATCH", "Binding risk gate differs from the complete derived contract"))
    expected_agent_conclusions = _agent_conclusions(
        derived_data_status == "PASS", conflict,
        derived_risk_status == "PASS", macro_verified,
    )
    if not _strict_json_equal(
        report.get("agent_conclusions"), expected_agent_conclusions
    ):
        issues.append(_issue(
            "AGENT_CONCLUSIONS_MISMATCH",
            "Agent conclusions differ from independently derived state",
        ))
    risk_gate_reasons = risk_gate.get("reasons") if isinstance(risk_gate, Mapping) else None
    if not _strict_json_equal(risk_gate_reasons, risk_reasons):
        issues.append(_issue("RISK_GATE_REASONS_MISMATCH", "Reported risk-gate reasons differ from independently derived reasons"))
    if risk_status in ("PASS", "FAIL") and risk_status != derived_risk_status:
        issues.append(_issue("RISK_GATE_MISMATCH", "Reported risk gate differs from independently derived state"))
    if derived_data_status == "PASS" and derived_risk_status == "FAIL" and decision not in ("WAIT", "NO_TRADE_DATA"):
        issues.append(_issue("RISK_GATE_OVERRIDE", "A failed independently derived risk gate must bind to WAIT"))
    if decision in ("LONG_SETUP", "SHORT_SETUP") and derived_risk_status != "PASS":
        issues.append(_issue("RISK_GATE_OVERRIDE", "A directional setup requires a passed independently derived risk gate"))
    if derived_data_status == "FAIL":
        expected_decision = "NO_TRADE_DATA"
    elif derived_risk_status == "FAIL":
        expected_decision = "WAIT"
    else:
        expected_decision = "LONG_SETUP" if derived_direction == "LONG" else "SHORT_SETUP"
    if decision != expected_decision:
        issues.append(_issue(
            "FINAL_DECISION_MISMATCH",
            "Final decision differs from the independently derived gate state and direction",
        ))
    if derived_risk_status == "PASS" and decision in ("LONG_SETUP", "SHORT_SETUP") and decision != expected_decision:
        issues.append(_issue("DECISION_DIRECTION_MISMATCH", "Final decision conflicts with proposed-level direction"))
    reported_reward_to_risk = (
        risk_gate.get("reward_to_risk", object())
        if isinstance(risk_gate, Mapping)
        else object()
    )
    reported_ratio = _finite_number(reported_reward_to_risk)
    ratio_matches = (
        reported_reward_to_risk is None
        if derived_reward_to_risk is None
        else (
            reported_ratio is not None
            and math.isclose(
                reported_ratio,
                derived_reward_to_risk,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        )
    )
    if not ratio_matches:
        issues.append(_issue(
            "REWARD_TO_RISK_MISMATCH",
            "Reported reward-to-risk differs from the independently derived value",
        ))
    expected_confidence = _confidence(
        derived_data_status == "PASS", conflict, macro_verified,
        derived_risk_status == "PASS",
    )
    derived_cap = expected_confidence["cap"]
    confidence = report.get("confidence", {})
    if isinstance(confidence, Mapping):
        components = confidence.get("components", {})
        component_total = 0.0
        components_valid = isinstance(components, Mapping)
        if isinstance(components, Mapping):
            for name, component_cap in CONFIDENCE_CAPS.items():
                value = _finite_number(components.get(name))
                if value is None:
                    components_valid = False
                    code = "CONFIDENCE_NON_FINITE" if isinstance(components.get(name), (int, float)) else "CONFIDENCE_COMPONENT_CAP"
                    issues.append(_issue(code, f"{name} must be a finite numeric component"))
                elif value < 0 or value > component_cap:
                    components_valid = False
                    issues.append(_issue("CONFIDENCE_COMPONENT_CAP", f"{name} exceeds its evidence cap"))
                else:
                    component_total += value
        else:
            issues.append(_issue("CONFIDENCE_COMPONENT_CAP", "Confidence components must be an object"))
        total = confidence.get("total", 0)
        finite_total = _finite_number(total)
        if finite_total is None:
            issues.append(_issue("CONFIDENCE_NON_FINITE", "Confidence total must be finite"))
        elif components_valid and not math.isclose(finite_total, component_total, rel_tol=1e-9, abs_tol=1e-9):
            issues.append(_issue("CONFIDENCE_TOTAL_MISMATCH", "Confidence total differs from the component sum"))
        if finite_total is not None and finite_total > derived_cap:
            issues.append(_issue("CONFIDENCE_TOTAL_CAP", "Confidence exceeds the applicable evidence cap"))
        if confidence.get("cap") != derived_cap:
            issues.append(_issue("CONFIDENCE_CAP_MISMATCH", "Reported confidence cap differs from the evidence-derived cap"))
        if (
            not isinstance(components, Mapping)
            or set(components) != set(expected_confidence["components"])
            or any(
                _finite_number(components.get(name)) != expected
                for name, expected in expected_confidence["components"].items()
            )
            or finite_total != expected_confidence["total"]
            or confidence.get("cap") != expected_confidence["cap"]
        ):
            issues.append(_issue("CONFIDENCE_COMPONENT_MISMATCH", "Confidence contract differs from independently derived evidence"))
    else:
        issues.append(_issue("CONFIDENCE_TOTAL_MISMATCH", "Confidence must be an object"))
        issues.append(_issue("CONFIDENCE_COMPONENT_MISMATCH", "Confidence contract differs from independently derived evidence"))

    levels = report.get("proposed_levels")
    if levels is not None:
        if not isinstance(levels, Mapping) or set(levels) != PROPOSED_LEVEL_FIELDS:
            issues.append(_issue("PROPOSED_LEVEL_FIELDS_MISMATCH", "Proposed levels must contain exactly the seven allowed fields"))
        else:
            canonical_levels, _ = _ground_levels(levels, snapshots)
            if not _strict_json_equal(levels, canonical_levels):
                issues.append(_issue("PROPOSED_LEVELS_MISMATCH", "Proposed levels are not strict-JSON-safe canonical values"))
    if isinstance(levels, Mapping) and (
        risk_status == "PASS" or decision in ("LONG_SETUP", "SHORT_SETUP")
    ):
        for name in ("entry", "stop", "target"):
            reference = levels.get(f"{name}_ref")
            supplied = _finite_number(levels.get(name))
            expected = _finite_number(_resolve_ref(snapshots, reference)) if isinstance(reference, str) and reference else None
            supported = supplied is not None and expected is not None and math.isclose(supplied, expected, rel_tol=1e-9, abs_tol=1e-9)
            if not supported:
                issues.append(_issue("UNSUPPORTED_LEVEL", f"{name} lacks matching calculated evidence"))
    elif decision in ("LONG_SETUP", "SHORT_SETUP"):
        issues.append(_issue("UNSUPPORTED_LEVEL", "Directional setup lacks grounded levels"))

    claims = report.get("market_claims", [])
    if not isinstance(claims, list):
        issues.append(_issue("INVALID_CLAIMS", "market_claims must be a list"))
        claims = []
    for index, claim in enumerate(claims):
        claim_id = claim.get("id", index) if isinstance(claim, Mapping) else index
        if not isinstance(claim, Mapping):
            issues.append(_issue("INVALID_CLAIM", "Claim must be an object", claim_id))
            continue
        allowed_fields = CLAIM_FIELDS | {"pair"}
        if frozenset(claim) not in {frozenset(CLAIM_FIELDS), frozenset(allowed_fields)}:
            issues.append(_issue(
                "CLAIM_FIELDS_MISMATCH",
                "Numerical claim fields differ from the exact Batch 1 contract",
                claim_id,
            ))
        claim_pair = claim.get("pair", pair)
        if claim_pair != pair:
            issues.append(_issue("CLAIM_PAIR_MISMATCH", "Claim pair differs from the report and isolated grounding", claim_id))
            continue
        if claim.get("kind") in {"volume", "obv", "order_flow", "liquidity", "institutional_activity"}:
            issues.append(_issue("UNSUPPORTED_SPOT_FX_VOLUME", "Zero-filled spot-FX volume cannot support this claim", claim_id))
            continue
        if claim.get("kind") not in {"price", "indicator"}:
            issues.append(_issue("UNSUPPORTED_CLAIM_KIND", "Numerical claim kind is not supported", claim_id))
            continue
        if not all(claim.get(field) for field in ("source", "observed_at", "evidence_ref")):
            issues.append(_issue("MISSING_PROVENANCE", "Numerical market claim lacks source, timestamp, or evidence reference", claim_id))
            continue
        if not _claim_reference_matches_kind(claim["kind"], claim["evidence_ref"]):
            issues.append(_issue(
                "CLAIM_KIND_REFERENCE_MISMATCH",
                "Claim kind and evidence reference do not satisfy the exact Batch 1 reference contract",
                claim_id,
            ))
            continue
        expected = _resolve_ref(snapshots, str(claim["evidence_ref"]))
        try:
            supplied = _finite_number(claim["value"])
        except KeyError:
            supplied = None
        grounded = _finite_number(expected)
        if supplied is None or grounded is None:
            issues.append(_issue("UNSUPPORTED_NUMERICAL_CLAIM", "Claim has no numerical grounding value", claim_id))
            continue
        if not math.isclose(supplied, grounded, rel_tol=1e-9, abs_tol=1e-9):
            issues.append(_issue("INVENTED_NUMERICAL_VALUE", "Claim value differs from injected/calculated evidence", claim_id))
            continue
        evidence_bar = _evidence_bar(rows, str(claim["evidence_ref"]))
        referenced_source = evidence_bar.get("source") if evidence_bar else None
        if not referenced_source or claim.get("source") != referenced_source:
            issues.append(_issue("CLAIM_SOURCE_MISMATCH", "Claim source differs from referenced injected evidence", claim_id))
        referenced_at = _iso_utc(evidence_bar.get("trade_date")) if evidence_bar else None
        claimed_at = _iso_utc(claim.get("observed_at"))
        if not referenced_at or not claimed_at or claimed_at != referenced_at:
            issues.append(_issue("CLAIM_TIMESTAMP_MISMATCH", "Claim timestamp differs from referenced injected evidence", claim_id))

    if report.get("recheck_condition") != RECHECK_CONDITION:
        issues.append(_issue("RECHECK_CONDITION_MISMATCH", "Recheck condition differs from the deterministic contract"))
    expected_disclosure = _disclosure(label) if isinstance(label, str) and label in EVIDENCE_LABELS else None
    if report.get("disclosure") != expected_disclosure:
        issues.append(_issue("DISCLOSURE_MISMATCH", "Disclosure is inconsistent with evidence_label or required limitations"))

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
    }


def _safe_audit_validation_report(
    report: object, grounding: object, *, trusted_provider_capture: bool = False
) -> dict:
    """Fail-safe audit wrapper; trust must be supplied by an internal caller."""
    try:
        result = _audit_validation_report(
            report,
            grounding,
            trusted_provider_capture=trusted_provider_capture,
        )
        json.dumps(result, allow_nan=False)
        return result
    except (ArithmeticError, KeyError, OSError, TypeError, ValueError, OverflowError):
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "passed": False,
            "issue_count": 1,
            "issues": [_issue("AUDIT_INPUT_UNSAFE", "Input could not be safely audited")],
        }


def audit_validation_report(report: object, grounding: object) -> dict:
    """Public audit boundary; provider capture is always untrusted here."""
    return _safe_audit_validation_report(report, grounding)


def build_validation_report(
    pair: object,
    grounding: object,
    *,
    run_id: object,
    evidence_label: object = "SIMULATED",
    provider: object = None,
    macro_evidence: object = None,
    proposed_levels: object = None,
) -> dict:
    """Fail-safe public report builder for all JSON-compatible inputs."""
    try:
        result = _build_validation_report(
            pair, grounding, run_id=run_id, evidence_label=evidence_label,
            provider=provider, macro_evidence=macro_evidence,
            proposed_levels=proposed_levels,
        )
        json.dumps(result, allow_nan=False)
        return result
    except (ArithmeticError, KeyError, OSError, TypeError, ValueError, OverflowError):
        return _build_validation_report(
            "INVALID/PAIR", {}, run_id=_json_safe_string(run_id, "invalid-run-id"),
            evidence_label=(
                evidence_label
                if isinstance(evidence_label, str) and evidence_label in EVIDENCE_LABELS
                else "SIMULATED"
            ),
        )


def build_captured_provider_validation_report(
    pair: object, grounding: object, *, run_id: object
) -> dict:
    """Trusted runtime/read-path builder for internally fetched public bars.

    API callers and model output have no route to select this function or its
    label. Provider approval and complete-history consistency remain binding.
    """
    try:
        result = _build_validation_report(
            pair, grounding, run_id=run_id, evidence_label="CAPTURED_PROVIDER",
            trusted_provider_capture=True,
        )
        json.dumps(result, allow_nan=False)
        return result
    except (ArithmeticError, KeyError, OSError, TypeError, ValueError, OverflowError):
        return _build_validation_report(
            "INVALID/PAIR", {}, run_id=_json_safe_string(run_id, "invalid-run-id")
        )
