"""Batch 1 deterministic TradeCoreFX report and adversarial contracts."""

from __future__ import annotations

from copy import deepcopy
import json
import math

import pandas as pd
import pytest

from src.swarm.tradecorefx_validation import (
    audit_validation_report,
    build_validation_report,
    format_fx_price,
    normalize_forex_pair,
)


def _rows(pair: str, *, count: int = 220, stale: str | None = None) -> dict[str, list[dict]]:
    result: list[dict] = []
    base = 150.0 if "JPY" in pair else 1.10
    step = 0.01 if "JPY" in pair else 0.0001
    frequencies = {"1D": "D", "4H": "4h", "1H": "h"}
    for timeframe, frequency in frequencies.items():
        timestamps = pd.date_range("2025-01-01", periods=count, freq=frequency, tz="UTC")
        for index, timestamp in enumerate(timestamps):
            close = base + index * step
            result.append({
                "trade_date": timestamp.isoformat(),
                "open": close - step / 2,
                "high": close + step,
                "low": close - step,
                "close": close,
                "volume": 0.0,
                "timeframe": timeframe,
                "source": "deterministic-fixture",
                "fetched_at": "2026-08-14T12:00:00+00:00",
                "market": "forex",
                "is_stale": stale == timeframe,
            })
    return {pair: result}


def _report(pair: str = "EUR/USD", grounding=None, **kwargs) -> dict:
    return build_validation_report(
        pair,
        grounding if grounding is not None else _rows(pair),
        run_id=f"fixture-{pair.replace('/', '-').lower()}",
        evidence_label="SIMULATED",
        **kwargs,
    )


def _grounded_levels(report: dict, direction: str = "LONG") -> dict:
    latest = report["indicator_evidence"]["1H"]["latest"]
    if direction == "LONG":
        names = {"entry": "open", "stop": "low", "target": "high"}
    else:
        names = {"entry": "open", "stop": "high", "target": "low"}
    return {
        "direction": direction,
        **{name: latest[field] for name, field in names.items()},
        **{f"{name}_ref": f"1H.latest.{field}" for name, field in names.items()},
    }


def _falling_rows(pair: str = "EUR/USD") -> dict[str, list[dict]]:
    grounding = _rows(pair)
    step = 0.01 if "JPY" in pair else 0.0001
    base = 180.0 if "JPY" in pair else 1.30
    for timeframe in ("1D", "4H", "1H"):
        rows = [row for row in grounding[pair] if row["timeframe"] == timeframe]
        for index, row in enumerate(rows):
            close = base - index * step
            row.update(open=close + step / 2, high=close + step, low=close - step, close=close)
    return grounding


VERIFIED_MACRO = {
    "claim": "Deterministic macro fixture",
    "source": "macro-fixture",
    "observed_at": "2026-08-14T11:00:00+00:00",
    "url": "https://example.invalid/macro-fixture",
}


@pytest.mark.parametrize("symbol", ["EUR/USD", "EURUSD.FX", "GBP/USD", "GBPUSD.FX", "USD/JPY", "USDJPY.FX"])
def test_multi_pair_forms_produce_isolated_complete_wait_reports(symbol: str) -> None:
    canonical = normalize_forex_pair(symbol)
    report = _report(symbol, _rows(symbol))

    assert report["pair"] == canonical
    assert report["evidence_label"] == "SIMULATED"
    assert report["final_decision"] == "WAIT"
    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["binding_risk_gate"]["status"] == "FAIL"
    assert report["macro"]["status"] == "UNAVAILABLE"
    assert report["audit"]["passed"]
    assert set(report["bar_evidence"]) == {"1D", "4H", "1H"}
    for evidence in report["bar_evidence"].values():
        assert evidence["source"] == "deterministic-fixture"
        assert evidence["captured_at"]
        assert evidence["last_bar_at"]
        assert evidence["freshness"] == "FRESH"
        assert evidence["history_status"] == "ok"


def test_pair_isolation_never_imports_another_pairs_evidence() -> None:
    grounding = {**_rows("EUR/USD"), **_rows("GBP/USD"), **_rows("USD/JPY")}
    report = _report("GBPUSD.FX", grounding)

    assert set(report["indicator_evidence"]) == {"1D", "4H", "1H"}
    assert report["indicator_evidence"]["1H"]["latest"]["close"] < 2
    assert "EUR/USD" not in str(report)
    assert "USD/JPY" not in str(report)


def test_fx_precision_contract() -> None:
    assert format_fx_price("EURUSD.FX", 1.082734) == "1.08273"
    assert format_fx_price("USD/JPY", 157.4321) == "157.432"


@pytest.mark.parametrize("missing", ["1D", "4H", "1H"])
def test_missing_required_timeframe_is_no_trade_data(missing: str) -> None:
    grounding = _rows("EUR/USD")
    grounding["EUR/USD"] = [row for row in grounding["EUR/USD"] if row["timeframe"] != missing]
    report = _report(grounding=grounding)
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert any("missing required timeframe" in reason for reason in report["binding_data_gate"]["reasons"])


def test_stale_intraday_data_is_no_trade_data() -> None:
    report = _report(grounding=_rows("EUR/USD", stale="1H"))
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert "1H: stale data" in report["binding_data_gate"]["reasons"]


def test_insufficient_indicator_history_is_no_trade_data() -> None:
    report = _report(grounding=_rows("EUR/USD", count=34))
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert all(item["history_status"] == "insufficient_data" for item in report["bar_evidence"].values())


def test_provider_empty_or_exception_outcome_is_no_trade_data() -> None:
    assert _report(grounding={})["final_decision"] == "NO_TRADE_DATA"
    # Provider exceptions are converted by grounding into an omitted pair.
    assert _report(grounding={"GBP/USD": []})["final_decision"] == "NO_TRADE_DATA"


@pytest.mark.parametrize("provider", [None, "deterministic-fixture", "conflicting-provider"])
def test_provider_argument_never_overrides_evidence(provider: str | None) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding, provider=provider)

    assert report["data_provider"] == "deterministic-fixture"
    assert report["audit"]["passed"]
    assert audit_validation_report(report, grounding)["passed"]


@pytest.mark.parametrize("pair", ["EURUSD", "EUR/EUR", "BAD", "EUR//USD"])
def test_unsupported_or_malformed_pair_is_safe(pair: str) -> None:
    assert _report(pair, _rows("EUR/USD"))["final_decision"] == "NO_TRADE_DATA"


def test_directional_conflict_is_wait() -> None:
    grounding = _rows("EUR/USD")
    daily = [row for row in grounding["EUR/USD"] if row["timeframe"] == "1D"]
    for index, row in enumerate(daily):
        close = 1.30 - index * 0.0005
        row.update(open=close + 0.0001, high=close + 0.0002, low=close - 0.0002, close=close)
    report = _report(grounding=grounding)
    assert report["final_decision"] == "WAIT"
    assert "1D/4H directional conflict" in report["binding_risk_gate"]["reasons"]


def test_low_or_invalid_reward_to_risk_is_wait() -> None:
    base = _report()
    latest = base["indicator_evidence"]["1H"]["latest"]
    refs = {f"{name}_ref": f"1H.latest.{name}" for name in ("close", "low", "high")}
    low = _report(proposed_levels={
        "direction": "LONG", "entry": latest["close"], "stop": latest["low"], "target": latest["high"],
        "entry_ref": refs["close_ref"], "stop_ref": refs["low_ref"], "target_ref": refs["high_ref"],
    })
    invalid = _report(proposed_levels={
        "direction": "SHORT", "entry": latest["close"], "stop": latest["low"], "target": latest["high"],
        "entry_ref": refs["close_ref"], "stop_ref": refs["low_ref"], "target_ref": refs["high_ref"],
    })
    assert low["final_decision"] == invalid["final_decision"] == "WAIT"
    assert "reward-to-risk below 2.0" in low["binding_risk_gate"]["reasons"]
    assert "entry/stop/target geometry is invalid" in invalid["binding_risk_gate"]["reasons"]


@pytest.mark.parametrize("direction", [None, "", "SIDEWAYS", "ARBITRARY"])
def test_invalid_direction_fails_binding_risk_gate(direction: str | None) -> None:
    levels = _grounded_levels(_report())
    levels.pop("direction")
    if direction is not None:
        levels["direction"] = direction
    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)

    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["macro"]["status"] == "VERIFIED"
    assert report["binding_risk_gate"]["status"] == "FAIL"
    assert report["binding_risk_gate"]["reasons"] == [
        "INVALID_DIRECTION: direction must be LONG or SHORT"
    ]
    assert report["binding_risk_gate"]["reward_to_risk"] is None
    assert report["final_decision"] == "WAIT"


@pytest.mark.parametrize(
    ("direction", "levels", "decision"),
    [
        (" long ", {"entry": 1.10, "stop": 1.09, "target": 1.12}, "LONG_SETUP"),
        (" short ", {"entry": 1.10, "stop": 1.11, "target": 1.08}, "SHORT_SETUP"),
    ],
)
def test_valid_normalized_direction_produces_setup(
    direction: str, levels: dict[str, float], decision: str
) -> None:
    grounding = _rows("EUR/USD") if decision == "LONG_SETUP" else _falling_rows()
    base_report = _report(grounding=grounding)
    grounded = _grounded_levels(base_report, "LONG" if decision == "LONG_SETUP" else "SHORT")
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels={**grounded, "direction": direction},
    )

    assert report["binding_risk_gate"]["status"] == "PASS"
    assert report["binding_risk_gate"]["reward_to_risk"] >= 2.0
    assert report["final_decision"] == decision
    assert report["audit"]["passed"]


@pytest.mark.parametrize("name", ["entry", "stop", "target"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf], ids=["nan", "pos_inf", "neg_inf"])
def test_non_finite_levels_fail_risk_gate_and_remain_strict_json(name: str, value: float) -> None:
    base = _report()
    levels = _grounded_levels(base)
    levels[name] = value
    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)

    assert report["final_decision"] == "WAIT"
    assert report["binding_risk_gate"]["status"] == "FAIL"
    assert "NON_FINITE_LEVEL: entry, stop, and target must be finite" in report["binding_risk_gate"]["reasons"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("name", ["entry", "stop", "target"])
@pytest.mark.parametrize("value", [10**10000, -(10**10000)], ids=["huge_positive", "huge_negative"])
def test_huge_integer_levels_fail_risk_gate_and_remain_strict_json(name: str, value: int) -> None:
    levels = _grounded_levels(_report())
    levels[name] = value

    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)

    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["binding_risk_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "WAIT"
    assert "NON_FINITE_LEVEL: entry, stop, and target must be finite" in report["binding_risk_gate"]["reasons"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("direction", [math.nan, math.inf, -math.inf, 42, ["LONG"]])
def test_non_json_safe_or_non_string_direction_is_safely_rejected(direction: object) -> None:
    levels = {**_grounded_levels(_report()), "direction": direction}
    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)

    assert report["proposed_levels"]["direction"] is None
    assert report["binding_risk_gate"]["reasons"] == [
        "INVALID_DIRECTION: direction must be LONG or SHORT"
    ]
    assert report["final_decision"] == "WAIT"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_proposed_levels_discard_unsupported_non_finite_fields() -> None:
    levels = {
        **_grounded_levels(_report()),
        "unexpected_nan": math.nan,
        "nested": {"infinity": math.inf},
    }
    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)

    assert set(report["proposed_levels"]) == {
        "direction", "entry", "stop", "target",
        "entry_ref", "stop_ref", "target_ref",
    }
    assert report["final_decision"] == "LONG_SETUP"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("missing_ref", ["entry_ref", "stop_ref", "target_ref"])
def test_missing_level_reference_is_wait(missing_ref: str) -> None:
    levels = _grounded_levels(_report())
    levels.pop(missing_ref)
    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)
    assert report["final_decision"] == "WAIT"
    assert "LEVEL_GROUNDING_FAILED: entry, stop, and target require matching evidence references" in report["binding_risk_gate"]["reasons"]


@pytest.mark.parametrize("reference", ["", "1H.unknown.value", "BAD.latest.close", 42])
def test_malformed_or_unsupported_level_reference_is_wait(reference: object) -> None:
    levels = _grounded_levels(_report())
    levels["entry_ref"] = reference
    report = _report(macro_evidence=VERIFIED_MACRO, proposed_levels=levels)
    assert report["final_decision"] == "WAIT"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_mismatched_and_arbitrary_levels_are_wait() -> None:
    grounded = _grounded_levels(_report())
    mismatched = {**grounded, "entry": grounded["entry"] + 0.01}
    arbitrary = {"direction": "LONG", "entry": 1.10, "stop": 1.09, "target": 1.12}
    assert _report(macro_evidence=VERIFIED_MACRO, proposed_levels=mismatched)["final_decision"] == "WAIT"
    assert _report(macro_evidence=VERIFIED_MACRO, proposed_levels=arbitrary)["final_decision"] == "WAIT"


def test_cross_pair_level_evidence_fails_audit() -> None:
    eur = _rows("EUR/USD")
    base = _report(grounding=eur)
    report = _report(grounding=eur, macro_evidence=VERIFIED_MACRO, proposed_levels=_grounded_levels(base))
    audit = audit_validation_report(report, _rows("GBP/USD"))
    assert not audit["passed"]
    assert "UNSUPPORTED_LEVEL" in {issue["code"] for issue in audit["issues"]}


@pytest.mark.parametrize("gate", ["binding_data_gate", "binding_risk_gate"])
@pytest.mark.parametrize("status", [None, "", "pass", "UNKNOWN", 1])
def test_missing_or_invalid_binding_gate_status_fails_audit(gate: str, status: object) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    if status is None:
        report[gate].pop("status")
    else:
        report[gate]["status"] = status
    audit = audit_validation_report(report, grounding)
    expected = "INVALID_DATA_GATE_STATUS" if gate == "binding_data_gate" else "INVALID_RISK_GATE_STATUS"
    assert not audit["passed"]
    assert expected in {issue["code"] for issue in audit["issues"]}


def test_directional_decision_with_missing_gates_fails_audit() -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report["final_decision"] = "LONG_SETUP"
    report.pop("binding_data_gate")
    report.pop("binding_risk_gate")
    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert {"INVALID_DATA_GATE_STATUS", "INVALID_RISK_GATE_STATUS", "DATA_GATE_OVERRIDE", "RISK_GATE_OVERRIDE"} <= codes


@pytest.mark.parametrize("mutation", ["stale", "missing_source", "malformed"])
def test_forged_pass_data_gate_cannot_override_grounding(mutation: str) -> None:
    grounding = _rows("EUR/USD", stale="1H") if mutation == "stale" else _rows("EUR/USD")
    if mutation == "missing_source":
        for row in grounding["EUR/USD"]:
            if row["timeframe"] == "4H":
                row["source"] = None
    elif mutation == "malformed":
        grounding["EUR/USD"] = [None]  # type: ignore[list-item]
    report = deepcopy(_report(grounding=grounding))
    report["binding_data_gate"] = {"status": "PASS", "reasons": []}

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert "DATA_GATE_MISMATCH" in {issue["code"] for issue in audit["issues"]}


@pytest.mark.parametrize(
    "mutation",
    ["invalid_geometry", "low_reward_to_risk", "directional_conflict"],
)
def test_forged_pass_risk_gate_cannot_override_derived_failure(mutation: str) -> None:
    grounding = _rows("EUR/USD")
    if mutation == "directional_conflict":
        daily = [row for row in grounding["EUR/USD"] if row["timeframe"] == "1D"]
        for index, row in enumerate(daily):
            close = 1.30 - index * 0.0005
            row.update(open=close + 0.0001, high=close + 0.0002, low=close - 0.0002, close=close)
    base = _report(grounding=grounding)
    levels = _grounded_levels(base)
    if mutation == "invalid_geometry":
        latest = base["indicator_evidence"]["1H"]["latest"]
        levels.update(
            stop=latest["high"], stop_ref="1H.latest.high",
            target=latest["low"], target_ref="1H.latest.low",
        )
    elif mutation == "low_reward_to_risk":
        latest = base["indicator_evidence"]["1H"]["latest"]
        levels.update(
            entry=latest["close"], entry_ref="1H.latest.close",
            stop=latest["low"], stop_ref="1H.latest.low",
            target=latest["high"], target_ref="1H.latest.high",
        )
    report = _report(grounding=grounding, macro_evidence=VERIFIED_MACRO, proposed_levels=levels)
    report["binding_risk_gate"] = {"status": "PASS", "reasons": [], "reward_to_risk": 2.0}
    report["final_decision"] = "LONG_SETUP"

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert {"RISK_GATE_MISMATCH", "RISK_GATE_OVERRIDE"} <= {
        issue["code"] for issue in audit["issues"]
    }


def test_forged_pass_risk_gate_cannot_override_decision_direction_mismatch() -> None:
    grounding = _rows("EUR/USD")
    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base, "LONG"),
    )
    report["binding_risk_gate"]["status"] = "PASS"
    report["final_decision"] = "SHORT_SETUP"

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert "DECISION_DIRECTION_MISMATCH" in {
        issue["code"] for issue in audit["issues"]
    }


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("decision", ["WAIT", "NO_TRADE_DATA"])
def test_valid_directional_setup_changed_to_safe_state_fails(
    direction: str, decision: str
) -> None:
    grounding = _rows("EUR/USD") if direction == "LONG" else _falling_rows()
    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base, direction),
    )
    report["final_decision"] = decision

    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "FINAL_DECISION_MISMATCH" in codes


@pytest.mark.parametrize(
    ("grounding", "decision"),
    [(_rows("EUR/USD", stale="1H"), "WAIT"), (_rows("EUR/USD"), "NO_TRADE_DATA")],
    ids=["data-fail-must-be-no-trade-data", "risk-fail-must-be-wait"],
)
def test_failed_gate_has_only_one_valid_safe_decision(
    grounding: dict[str, list[dict]], decision: str
) -> None:
    report = _report(grounding=grounding)
    report["final_decision"] = decision
    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "FINAL_DECISION_MISMATCH" in codes


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_wrong_directional_decision_fails_complete_decision_audit(direction: str) -> None:
    grounding = _rows("EUR/USD") if direction == "LONG" else _falling_rows()
    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base, direction),
    )
    report["final_decision"] = "SHORT_SETUP" if direction == "LONG" else "LONG_SETUP"
    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert {"FINAL_DECISION_MISMATCH", "DECISION_DIRECTION_MISMATCH"} <= codes


@pytest.mark.parametrize(
    "value",
    [pytest.param("missing", id="missing"), 3.0, math.nan, math.inf, -math.inf, "2.0", True],
)
def test_reported_reward_to_risk_must_match_derived_value(value: object) -> None:
    grounding = _rows("EUR/USD")
    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base),
    )
    if value == "missing":
        report["binding_risk_gate"].pop("reward_to_risk")
    else:
        report["binding_risk_gate"]["reward_to_risk"] = value
    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "REWARD_TO_RISK_MISMATCH" in codes


def test_correct_derived_reward_to_risk_passes_audit() -> None:
    grounding = _rows("EUR/USD")
    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base),
    )
    assert report["binding_risk_gate"]["reward_to_risk"] is not None
    assert audit_validation_report(report, grounding)["passed"]


@pytest.mark.parametrize("value", [2.0, math.nan, math.inf])
def test_unexpected_reward_to_risk_fails_when_none_is_derived(value: float) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    report["binding_risk_gate"]["reward_to_risk"] = value
    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "REWARD_TO_RISK_MISMATCH" in codes


def test_macro_agent_conclusion_matches_verified_evidence() -> None:
    report = _report(macro_evidence=VERIFIED_MACRO)
    conclusion = report["agent_conclusions"]["fx_macro_catalyst_analyst"]
    assert conclusion == {
        "current_macro": "VERIFIED",
        "conclusion": "Verified current macro source was injected",
    }


def test_macro_agent_conclusion_remains_unavailable_without_valid_evidence() -> None:
    report = _report()
    conclusion = report["agent_conclusions"]["fx_macro_catalyst_analyst"]
    assert conclusion == {
        "current_macro": "UNAVAILABLE",
        "conclusion": "No verified current macro source was injected",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "structure", "momentum", "macro", "risk", "chief",
        "missing_agent", "extra_agent", "missing_field", "extra_field", "wrong_type",
    ],
)
def test_agent_conclusions_must_exactly_match_derived_state(mutation: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    agents = report["agent_conclusions"]
    names = list(agents)
    if mutation in {"structure", "momentum", "macro", "risk", "chief"}:
        name = names[["structure", "momentum", "macro", "risk", "chief"].index(mutation)]
        agents[name]["conclusion"] = "contradictory"
    elif mutation == "missing_agent":
        agents.pop(names[0])
    elif mutation == "extra_agent":
        agents["fabricated_agent"] = {"conclusion": "PASS"}
    elif mutation == "missing_field":
        agents[names[0]].pop("data_gate")
    elif mutation == "extra_field":
        agents[names[0]]["fabricated"] = True
    else:
        report["agent_conclusions"] = []

    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "AGENT_CONCLUSIONS_MISMATCH" in codes


@pytest.mark.parametrize("state", ["macro", "data_gate", "risk_gate"])
def test_agent_conclusions_cannot_contradict_independent_gates(state: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    if state == "macro":
        report["agent_conclusions"]["fx_macro_catalyst_analyst"]["current_macro"] = "VERIFIED"
    elif state == "data_gate":
        report["agent_conclusions"]["fx_structure_analyst"]["data_gate"] = "FAIL"
    else:
        report["agent_conclusions"]["fx_risk_gatekeeper"]["risk_gate"] = "PASS"

    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "AGENT_CONCLUSIONS_MISMATCH" in codes


def test_verified_macro_without_claim_is_unverified_for_wait_report() -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report["macro"] = {**VERIFIED_MACRO, "status": "VERIFIED"}
    report["macro"].pop("claim")

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert "UNVERIFIED_MACRO" in {issue["code"] for issue in audit["issues"]}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observed_at", "not-a-timestamp"),
        ("observed_at", "2026-08-14T11:00:00"),
        ("url", "/macro-fixture"),
        ("url", "://malformed"),
        ("url", "http://example.invalid/macro-fixture"),
        ("url", "https:///macro-fixture"),
        ("claim", 123),
        ("source", ["macro-fixture"]),
    ],
)
def test_invalid_macro_evidence_cannot_verify_or_enable_setup(field: str, value: object) -> None:
    grounding = _rows("EUR/USD")
    levels = _grounded_levels(_report(grounding=grounding))
    report = _report(
        grounding=grounding,
        macro_evidence={**VERIFIED_MACRO, field: value},
        proposed_levels=levels,
    )

    assert report["macro"]["status"] == "UNAVAILABLE"
    assert report["confidence"]["cap"] == 50
    assert report["final_decision"] == "WAIT"
    assert report["audit"]["passed"]


def test_valid_https_macro_evidence_still_enables_setup() -> None:
    grounding = _rows("EUR/USD")
    levels = _grounded_levels(_report(grounding=grounding))
    report = _report(grounding=grounding, macro_evidence=VERIFIED_MACRO, proposed_levels=levels)

    assert report["macro"]["status"] == "VERIFIED"
    assert report["confidence"]["cap"] == 100
    assert report["final_decision"] == "LONG_SETUP"
    assert report["audit"]["passed"]


@pytest.mark.parametrize(
    ("observed_at", "verified"),
    [
        ("2026-08-13T12:00:00+00:00", True),
        ("2026-08-13T11:59:59+00:00", False),
        ("2026-08-14T12:00:01+00:00", False),
    ],
    ids=["exactly-24-hours", "older-than-24-hours", "future"],
)
def test_macro_freshness_is_bound_to_valid_evidence_capture(
    observed_at: str, verified: bool
) -> None:
    grounding = _rows("EUR/USD")
    levels = _grounded_levels(_report(grounding=grounding))
    report = _report(
        grounding=grounding,
        macro_evidence={**VERIFIED_MACRO, "observed_at": observed_at},
        proposed_levels=levels,
    )

    assert (report["macro"]["status"] == "VERIFIED") is verified
    assert report["confidence"]["cap"] == (100 if verified else 50)
    assert report["final_decision"] == ("LONG_SETUP" if verified else "WAIT")
    assert report["audit"]["passed"]


def test_macro_cannot_verify_without_independently_valid_capture_time() -> None:
    grounding = _rows("EUR/USD")
    for row in grounding["EUR/USD"]:
        row["fetched_at"] = "malformed"
    report = _report(grounding=grounding, macro_evidence=VERIFIED_MACRO)

    assert report["capture_time"] is None
    assert report["macro"]["status"] == "UNAVAILABLE"
    assert report["confidence"]["cap"] == 50
    assert report["final_decision"] == "NO_TRADE_DATA"


@pytest.mark.parametrize(
    ("field", "timestamp"),
    [
        ("trade_date", "0001-01-01T00:00:00+14:00"),
        ("fetched_at", "9999-12-31T23:59:59-14:00"),
        ("fetched_at", "0001-01-01T00:00:00+14:00"),
    ],
    ids=["trade-date-underflow", "fetched-at-overflow", "fetched-at-underflow"],
)
def test_overflowing_bar_timestamps_fail_build_and_audit_safely(
    field: str, timestamp: str
) -> None:
    grounding = _rows("EUR/USD")
    for row in grounding["EUR/USD"]:
        row[field] = timestamp

    report = _report(grounding=grounding, macro_evidence=VERIFIED_MACRO)
    audit = audit_validation_report(report, grounding)

    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["macro"]["status"] == "UNAVAILABLE"
    assert audit["passed"]
    json.dumps(report, allow_nan=False)
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize(
    ("capture_at", "observed_at"),
    [
        ("0001-01-01T00:00:00+00:00", "0001-01-01T00:00:00+14:00"),
        ("9999-12-31T23:59:59+00:00", "9999-12-31T23:59:59-14:00"),
        ("0001-01-01T00:00:00+14:00", "0001-01-01T00:00:00+14:00"),
    ],
    ids=["macro-underflow", "macro-overflow", "capture-and-macro-underflow"],
)
def test_boundary_macro_timestamps_fail_build_and_audit_safely(
    capture_at: str, observed_at: str
) -> None:
    grounding = _rows("EUR/USD")
    for row in grounding["EUR/USD"]:
        row["fetched_at"] = capture_at
    report = _report(
        grounding=grounding,
        macro_evidence={**VERIFIED_MACRO, "observed_at": observed_at},
    )
    claimed = deepcopy(report)
    claimed["macro"] = {
        **VERIFIED_MACRO,
        "status": "VERIFIED",
        "observed_at": observed_at,
    }

    audit = audit_validation_report(claimed, grounding)

    assert report["macro"]["status"] == "UNAVAILABLE"
    assert report["final_decision"] in {"WAIT", "NO_TRADE_DATA"}
    assert not audit["passed"]
    assert "UNVERIFIED_MACRO" in {issue["code"] for issue in audit["issues"]}
    json.dumps(report, allow_nan=False)
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize(
    "observed_at", ["2026-08-13T11:59:59+00:00", "2026-08-14T12:00:01+00:00"]
)
def test_auditor_rejects_claimed_verified_stale_or_future_macro(observed_at: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report["macro"] = {**VERIFIED_MACRO, "status": "VERIFIED", "observed_at": observed_at}

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert "UNVERIFIED_MACRO" in {issue["code"] for issue in audit["issues"]}


def test_invalid_newest_ohlc_fails_gate_without_advertising_rejected_metadata() -> None:
    grounding = _rows("EUR/USD")
    previous = [row for row in grounding["EUR/USD"] if row["timeframe"] == "1H"][-1]
    grounding["EUR/USD"].append({
        **previous,
        "trade_date": "2026-08-14T13:00:00+00:00",
        "fetched_at": "2026-08-14T13:01:00+00:00",
        "source": "rejected-newest-source",
        "high": float("nan"),
    })
    report = _report(grounding=grounding)

    evidence = report["bar_evidence"]["1H"]
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["binding_data_gate"]["status"] == "FAIL"
    assert any("LATEST_BAR_INVALID" in reason for reason in report["binding_data_gate"]["reasons"])
    assert evidence["source"] == "deterministic-fixture"
    assert evidence["captured_at"] == "2026-08-14T12:00:00+00:00"
    assert evidence["last_bar_at"] == previous["trade_date"]
    assert evidence["bars"] == 220
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize("value", [10**10000, -(10**10000)], ids=["huge_positive", "huge_negative"])
def test_huge_integer_ohlc_fails_data_gate_without_raising(field: str, value: int) -> None:
    grounding = _rows("EUR/USD")
    newest = [row for row in grounding["EUR/USD"] if row["timeframe"] == "1H"][-1]
    newest[field] = value

    report = _report(grounding=grounding)

    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert any("LATEST_BAR_INVALID" in reason for reason in report["binding_data_gate"]["reasons"])
    json.dumps(report, allow_nan=False)


def test_valid_newest_ohlc_metadata_matches_indicators_and_passes() -> None:
    grounding = _rows("EUR/USD")
    newest = [row for row in grounding["EUR/USD"] if row["timeframe"] == "1H"][-1]
    report = _report(grounding=grounding)

    evidence = report["bar_evidence"]["1H"]
    assert report["binding_data_gate"]["status"] == "PASS"
    assert evidence["source"] == newest["source"]
    assert evidence["captured_at"] == newest["fetched_at"]
    assert evidence["last_bar_at"] == newest["trade_date"]
    assert evidence["bars"] == report["indicator_evidence"]["1H"]["bars"]
    assert report["audit"]["passed"]


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("indicator_evidence", "price"),
        ("bar_evidence", "source"),
        ("bar_evidence", "captured_at"),
        ("bar_evidence", "last_bar_at"),
        ("bar_evidence", "freshness"),
        ("bar_evidence", "missing_field"),
        ("bar_evidence", "extra_field"),
        ("indicator_evidence", "indicator"),
        ("indicator_evidence", "missing_field"),
        ("indicator_evidence", "extra_field"),
    ],
)
def test_complete_reported_evidence_must_match_grounding(field: str, mutation: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    evidence = report[field]["1H"]
    if mutation == "price":
        evidence["latest"]["close"] += 0.01
    elif mutation == "source":
        evidence["source"] = "altered-source"
    elif mutation in {"captured_at", "last_bar_at"}:
        evidence[mutation] = "2020-01-01T00:00:00+00:00"
    elif mutation == "freshness":
        evidence["freshness"] = "STALE"
    elif mutation == "indicator":
        evidence["momentum"]["rsi14"] -= 1.0
    elif mutation == "missing_field":
        evidence.pop(next(iter(evidence)))
    else:
        evidence["fabricated"] = 1

    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert ("BAR_EVIDENCE_MISMATCH" if field == "bar_evidence" else "INDICATOR_EVIDENCE_MISMATCH") in codes


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("capture_time", "2020-01-01T00:00:00+00:00", "CAPTURE_TIME_MISMATCH"),
        ("data_provider", "altered-provider", "DATA_PROVIDER_MISMATCH"),
    ],
)
def test_top_level_evidence_metadata_must_match_grounding(field: str, value: str, code: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report[field] = value
    assert code in {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}


@pytest.mark.parametrize("gate", ["binding_data_gate", "binding_risk_gate"])
@pytest.mark.parametrize("mutation", ["removed", "added", "reordered", "duplicated", "non_list"])
def test_complete_binding_gate_reasons_must_match(gate: str, mutation: str) -> None:
    grounding = _rows("EUR/USD")
    if gate == "binding_data_gate":
        for row in grounding["EUR/USD"]:
            row["is_stale"] = True
    report = deepcopy(_report(grounding=grounding))
    reasons = report[gate]["reasons"]
    assert len(reasons) >= 2
    if mutation == "removed":
        reasons.pop()
    elif mutation == "added":
        reasons.append("fabricated reason")
    elif mutation == "reordered":
        reasons[0], reasons[1] = reasons[1], reasons[0]
    elif mutation == "duplicated":
        reasons.append(reasons[0])
    else:
        report[gate]["reasons"] = tuple(reasons)

    expected = "DATA_GATE_REASONS_MISMATCH" if gate == "binding_data_gate" else "RISK_GATE_REASONS_MISMATCH"
    assert expected in {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}


@pytest.mark.parametrize("decision", ["WAIT", "NO_TRADE_DATA", "LONG_SETUP", "SHORT_SETUP"])
def test_untampered_reports_for_every_decision_still_pass_audit(decision: str) -> None:
    grounding = _falling_rows() if decision == "SHORT_SETUP" else _rows("EUR/USD")
    if decision == "NO_TRADE_DATA":
        grounding = _rows("EUR/USD", stale="1H")
    if decision in {"LONG_SETUP", "SHORT_SETUP"}:
        base = _report(grounding=grounding)
        report = _report(
            grounding=grounding,
            macro_evidence=VERIFIED_MACRO,
            proposed_levels=_grounded_levels(base, decision.removesuffix("_SETUP")),
        )
    else:
        report = _report(grounding=grounding)
    assert report["final_decision"] == decision
    assert audit_validation_report(report, grounding)["passed"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("observed_at", "bad"), ("observed_at", "2026-08-14T11:00:00"), ("url", "http://example.invalid")],
)
def test_auditor_rejects_claimed_verified_invalid_macro(field: str, value: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report["macro"] = {**VERIFIED_MACRO, "status": "VERIFIED", field: value}

    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "UNVERIFIED_MACRO" in codes


def test_caller_controlled_confidence_cap_cannot_inflate_authority() -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report["confidence"].update(cap=100, total=60)
    report["confidence"]["components"] = {"data": 25, "alignment": 20, "timing": 8, "momentum": 7, "macro": 0, "risk_reward": 0}
    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert {"CONFIDENCE_CAP_MISMATCH", "CONFIDENCE_TOTAL_CAP"} <= codes


@pytest.mark.parametrize(
    "mutation",
    ["macro", "risk_reward", "data", "missing", "extra", "altered"],
)
def test_confidence_must_match_independently_derived_evidence(mutation: str) -> None:
    if mutation == "data":
        grounding = _rows("EUR/USD", stale="1H")
    else:
        grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    components = report["confidence"]["components"]
    if mutation == "macro":
        components["macro"] = 10
    elif mutation == "risk_reward":
        components["risk_reward"] = 10
    elif mutation == "data":
        components["data"] = 25
    elif mutation == "missing":
        components.pop("momentum")
    elif mutation == "extra":
        components["invented"] = 0
    else:
        components["timing"] = 7
    report["confidence"]["total"] = sum(components.values())

    codes = {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}
    assert "CONFIDENCE_COMPONENT_MISMATCH" in codes


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("total_mismatch", "CONFIDENCE_TOTAL_MISMATCH"),
        ("excessive_component", "CONFIDENCE_COMPONENT_CAP"),
        ("negative_component", "CONFIDENCE_COMPONENT_CAP"),
        ("nan_component", "CONFIDENCE_NON_FINITE"),
        ("inf_total", "CONFIDENCE_NON_FINITE"),
    ],
)
def test_invalid_confidence_contract_fails(mutation: str, code: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    if mutation == "total_mismatch":
        report["confidence"]["total"] += 1
    elif mutation == "excessive_component":
        report["confidence"]["components"]["timing"] = 16
    elif mutation == "negative_component":
        report["confidence"]["components"]["data"] = -1
    elif mutation == "nan_component":
        report["confidence"]["components"]["data"] = math.nan
    else:
        report["confidence"]["total"] = math.inf
    assert code in {issue["code"] for issue in audit_validation_report(report, grounding)["issues"]}


@pytest.mark.parametrize("field", ["component", "total"])
@pytest.mark.parametrize("value", [10**10000, -(10**10000)], ids=["huge_positive", "huge_negative"])
def test_huge_integer_confidence_fails_audit_without_raising(field: str, value: int) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    if field == "component":
        report["confidence"]["components"]["data"] = value
    else:
        report["confidence"]["total"] = value

    audit = audit_validation_report(report, grounding)

    assert not audit["passed"]
    assert "CONFIDENCE_NON_FINITE" in {issue["code"] for issue in audit["issues"]}
    json.dumps(audit, allow_nan=False)


def test_valid_reports_use_independently_derived_50_and_100_caps() -> None:
    grounding = _rows("EUR/USD")
    unavailable = _report(grounding=grounding)
    assert unavailable["confidence"]["cap"] == 50
    assert audit_validation_report(unavailable, grounding)["passed"]

    base = _report(grounding=grounding)
    verified = _report(grounding=grounding, macro_evidence=VERIFIED_MACRO, proposed_levels=_grounded_levels(base))
    assert verified["confidence"]["cap"] == 100
    assert audit_validation_report(verified, grounding)["passed"]


@pytest.mark.parametrize("scenario", ["flat_weak_momentum", "volatility_spike", "abnormal_gap", "normal_weekend_gap", "unverified_macro_catalyst"])
def test_valid_but_non_actionable_difficult_markets_wait(scenario: str) -> None:
    # The local-safe beta has no verified current-macro dependency, so valid
    # fixtures remain WAIT regardless of the non-actionable market shape.
    report = _report()
    assert report["final_decision"] == "WAIT", scenario
    assert "verified current macro support unavailable" in report["binding_risk_gate"]["reasons"]


def test_grounded_numerical_claim_passes_audit() -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    close = report["indicator_evidence"]["1H"]["latest"]["close"]
    report["market_claims"] = [{
        "id": "latest-1h-close",
        "kind": "price",
        "value": close,
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": "1H.latest.close",
    }]
    assert audit_validation_report(report, grounding)["passed"]


@pytest.mark.parametrize(
    ("kind", "reference", "value_path"),
    [
        ("price", "1H.indicators.rsi14", ("indicators", "rsi14")),
        ("indicator", "1H.latest.close", ("latest", "close")),
    ],
)
def test_claim_kind_reference_mismatch_fails_external_audit(
    kind: str, reference: str, value_path: tuple[str, str]
) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    value = report["indicator_evidence"]["1H"][value_path[0]][value_path[1]]
    report["market_claims"] = [{
        "id": "forged-kind-reference", "kind": kind, "value": value,
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": reference,
    }]

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert "CLAIM_KIND_REFERENCE_MISMATCH" in {item["code"] for item in audit["issues"]}
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize(
    "reference",
    ["1h.latest.close", "2H.latest.close", "1H.latest", "1H.latest.close.extra",
     "1H.unknown.close", "1H.indicators.unknown", 7],
)
def test_malformed_or_unknown_claim_reference_shape_fails(reference: object) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    report["market_claims"] = [{
        "id": "bad-reference", "kind": "price", "value": 1.0,
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": reference,
    }]
    audit = audit_validation_report(report, grounding)
    assert "CLAIM_KIND_REFERENCE_MISMATCH" in {item["code"] for item in audit["issues"]}
    json.dumps(audit, allow_nan=False)


def test_legitimate_indicator_claim_passes_audit() -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    report["market_claims"] = [{
        "id": "rsi", "kind": "indicator",
        "value": report["indicator_evidence"]["1D"]["indicators"]["rsi14"],
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1D"]["last_bar_at"],
        "evidence_ref": "1D.indicators.rsi14",
    }]
    assert audit_validation_report(report, grounding)["passed"]


@pytest.mark.parametrize("value", [10**10000, -(10**10000)], ids=["huge_positive", "huge_negative"])
def test_huge_integer_numerical_claim_is_unsupported_without_raising(value: int) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    report["market_claims"] = [{
        "id": "overflowing-claim",
        "kind": "price",
        "value": value,
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": "1H.latest.close",
    }]

    audit = audit_validation_report(report, grounding)

    assert not audit["passed"]
    assert "UNSUPPORTED_NUMERICAL_CLAIM" in {issue["code"] for issue in audit["issues"]}
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("fabricated_source", "CLAIM_SOURCE_MISMATCH"),
        ("fabricated_timestamp", "CLAIM_TIMESTAMP_MISMATCH"),
        ("malformed_timestamp", "CLAIM_TIMESTAMP_MISMATCH"),
    ],
)
def test_claim_provenance_must_match_referenced_injected_bar(
    mutation: str, code: str
) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    claim = {
        "id": mutation,
        "kind": "price",
        "value": report["indicator_evidence"]["1H"]["latest"]["close"],
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": "1H.latest.close",
    }
    if mutation == "fabricated_source":
        claim["source"] = "fabricated-source"
    elif mutation == "fabricated_timestamp":
        claim["observed_at"] = "2025-01-01T00:00:00Z"
    else:
        claim["observed_at"] = "not-a-timestamp"
    report["market_claims"] = [claim]

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert code in {issue["code"] for issue in audit["issues"]}


def test_claim_evidence_from_another_pair_fails() -> None:
    eur_grounding = _rows("EUR/USD")
    gbp_grounding = _rows("GBP/USD")
    report = _report("EUR/USD", eur_grounding)
    report["market_claims"] = [{
        "id": "other-pair",
        "kind": "price",
        "value": report["indicator_evidence"]["1H"]["latest"]["close"],
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": "1H.latest.close",
    }]

    audit = audit_validation_report(report, gbp_grounding)
    assert not audit["passed"]
    assert "UNSUPPORTED_NUMERICAL_CLAIM" in {
        issue["code"] for issue in audit["issues"]
    }


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("invented_price", "INVENTED_NUMERICAL_VALUE"),
        ("unsupported_level", "CLAIM_KIND_REFERENCE_MISMATCH"),
        ("missing_provenance", "MISSING_PROVENANCE"),
        ("volume_order_flow", "UNSUPPORTED_SPOT_FX_VOLUME"),
        ("confidence", "CONFIDENCE_TOTAL_CAP"),
        ("unverified_macro", "UNVERIFIED_MACRO"),
        ("risk_override", "RISK_GATE_OVERRIDE"),
    ],
)
def test_adversarial_claims_fail_machine_readable_audit(mutation: str, code: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    claim = {"id": mutation, "kind": "price", "value": 1.0, "source": "deterministic-fixture", "observed_at": "2026-01-01T00:00:00Z", "evidence_ref": "1H.latest.close"}
    if mutation == "invented_price":
        report["market_claims"] = [claim]
    elif mutation == "unsupported_level":
        claim["evidence_ref"] = "1H.levels.entry"
        report["market_claims"] = [claim]
    elif mutation == "missing_provenance":
        claim.pop("source")
        report["market_claims"] = [claim]
    elif mutation == "volume_order_flow":
        claim["kind"] = "order_flow"
        report["market_claims"] = [claim]
    elif mutation == "confidence":
        report["confidence"]["total"] = 99
    elif mutation == "unverified_macro":
        report["macro"] = {"status": "VERIFIED", "claim": "claim", "source": None, "observed_at": None, "url": None}
    else:
        report["final_decision"] = "LONG_SETUP"
    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert code in {issue["code"] for issue in audit["issues"]}


JSON_EDGE_VALUES = [
    None, True, False, "", "bad", [], [1, {"x": None}], {}, {"x": 1},
    10**10000, -(10**10000), 1e308, -1e308, math.nan, math.inf, -math.inf,
]


@pytest.mark.parametrize("value", JSON_EDGE_VALUES, ids=lambda value: type(value).__name__)
def test_public_functions_are_fail_safe_for_json_edge_values(value: object) -> None:
    report = build_validation_report(
        value, value, run_id=value, evidence_label=value,
        provider=value, macro_evidence=value, proposed_levels=value,
    )
    audit = audit_validation_report(value, value)

    assert report["final_decision"] in {"WAIT", "NO_TRADE_DATA"}
    assert report["evidence_label"] in {"SIMULATED", "CAPTURED_FIXTURE"}
    assert isinstance(report["run_id"], str) and report["run_id"]
    json.dumps(report, allow_nan=False)
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("schema_version", "wrong", "SCHEMA_VERSION_MISMATCH"),
        ("evidence_label", "LIVE", "EVIDENCE_LABEL_INVALID"),
        ("pair", "GBP/USD", "BAR_EVIDENCE_MISMATCH"),
        ("timeframes", ["1D", "1H"], "TIMEFRAMES_MISMATCH"),
        ("run_id", "", "RUN_ID_INVALID"),
        ("data_provider", "wrong", "DATA_PROVIDER_MISMATCH"),
        ("capture_time", "bad", "CAPTURE_TIME_MISMATCH"),
        ("bar_evidence", {}, "BAR_EVIDENCE_MISMATCH"),
        ("indicator_evidence", {}, "INDICATOR_EVIDENCE_MISMATCH"),
        ("agent_conclusions", {}, "AGENT_CONCLUSIONS_MISMATCH"),
        ("dissent", ["fabricated"], "DISSENT_MISMATCH"),
        ("binding_data_gate", {}, "DATA_GATE_CONTRACT_MISMATCH"),
        ("binding_risk_gate", {}, "RISK_GATE_CONTRACT_MISMATCH"),
        ("confidence", {}, "CONFIDENCE_COMPONENT_MISMATCH"),
        ("macro", {}, "MACRO_CONTRACT_MISMATCH"),
        ("proposed_levels", {}, "PROPOSED_LEVEL_FIELDS_MISMATCH"),
        ("final_decision", "LONG_SETUP", "FINAL_DECISION_MISMATCH"),
        ("recheck_condition", "later", "RECHECK_CONDITION_MISMATCH"),
        ("disclosure", "guaranteed returns", "DISCLOSURE_MISMATCH"),
    ],
)
def test_every_security_relevant_top_level_field_is_independently_audited(
    field: str, value: object, code: str
) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report[field] = value
    audit = audit_validation_report(report, grounding)

    assert not audit["passed"]
    assert code in {issue["code"] for issue in audit["issues"]}
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_report_top_level_field_set_is_exact(mutation: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    if mutation == "missing":
        report.pop("disclosure")
    else:
        report["fabricated"] = True
    assert "REPORT_FIELDS_MISMATCH" in {
        issue["code"] for issue in audit_validation_report(report, grounding)["issues"]
    }


@pytest.mark.parametrize(
    "macro",
    [
        {"status": "UNAVAILABLE", "claim": "contradiction", "source": None, "observed_at": None, "url": None},
        {"status": "UNAVAILABLE", "claim": None, "source": None, "observed_at": None},
        {"status": "UNAVAILABLE", "claim": None, "source": None, "observed_at": None, "url": None, "extra": 1},
        {**VERIFIED_MACRO, "status": "VERIFIED", "extra": 1},
        {**VERIFIED_MACRO, "status": "VERIFIED", "url": "https://"},
        {**VERIFIED_MACRO, "status": "VERIFIED", "observed_at": "2026-08-14T13:00:00+00:00"},
    ],
)
def test_macro_contract_rejects_inexact_or_contradictory_states(macro: dict) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report["macro"] = macro
    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert {"MACRO_CONTRACT_MISMATCH", "UNVERIFIED_MACRO"} & {
        issue["code"] for issue in audit["issues"]
    }


def test_live_label_is_never_emitted_and_disclosure_is_deterministic() -> None:
    report = build_validation_report(
        "EUR/USD", _rows("EUR/USD"), run_id="fixture", evidence_label="LIVE"
    )
    assert report["evidence_label"] == "SIMULATED"
    assert report["disclosure"] == (
        "Decision support only; no broker execution or guarantee. "
        "Evidence is labelled SIMULATED."
    )
    assert report["audit"]["passed"]


def test_non_finite_claim_id_cannot_break_strict_audit_serialization() -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    report["market_claims"] = [{"id": math.nan, "kind": "price"}]
    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize("field", ["trade_date", "open", "high", "low", "close"])
def test_any_invalid_matching_bar_row_removes_directional_authority(field: str) -> None:
    grounding = _rows("EUR/USD")
    historical = next(
        row for row in grounding["EUR/USD"] if row["timeframe"] == "1H"
    )
    historical[field] = "malformed" if field == "trade_date" else math.nan

    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base),
    )

    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert any(
        "BAR_ROW_INVALID" in reason
        for reason in report["binding_data_gate"]["reasons"]
    )
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    "malformed",
    [None, True, 7, 1.25, "not-a-bar", ["not", "a", "bar"]],
    ids=["null", "boolean", "integer", "number", "string", "array"],
)
def test_non_object_selected_pair_rows_are_rejected_evidence(malformed: object) -> None:
    grounding = _rows("EUR/USD")
    grounding["EUR/USD"].insert(1, malformed)

    base = _report(grounding=grounding)
    report = _report(
        grounding=grounding,
        macro_evidence=VERIFIED_MACRO,
        proposed_levels=_grounded_levels(base),
    )

    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["binding_data_gate"]["reasons"][-1].startswith(
        "selected pair row 1: BAR_ROW_INVALID"
    )
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_non_object_row_for_another_pair_does_not_contaminate_selected_pair() -> None:
    grounding = {**_rows("EUR/USD"), **_rows("GBP/USD")}
    grounding["GBP/USD"].extend([None, False, 3, "bad", []])

    report = _report(grounding=grounding)

    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["final_decision"] == "WAIT"
    assert not any(
        "BAR_ROW_INVALID" in reason
        for reason in report["binding_data_gate"]["reasons"]
    )
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    "timeframe",
    [pytest.param("missing", id="missing"), None, True, 7, "", " 1H", "1H ", "1h", "2H"],
)
def test_invalid_selected_pair_mapping_timeframe_is_index_stable_safe_state(
    timeframe: object,
) -> None:
    grounding = _rows("EUR/USD")
    row = deepcopy(grounding["EUR/USD"][0])
    if timeframe == "missing":
        row.pop("timeframe")
    else:
        row["timeframe"] = timeframe
    grounding["EUR/USD"].insert(1, row)

    report = _report(grounding=grounding)

    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["binding_data_gate"]["reasons"][-1].startswith(
        "selected pair row 1: BAR_ROW_INVALID"
    )
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_invalid_mapping_timeframe_for_another_pair_remains_isolated() -> None:
    grounding = {**_rows("EUR/USD"), **_rows("GBP/USD")}
    grounding["GBP/USD"].append({"timeframe": "1h"})

    report = _report(grounding=grounding)

    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["final_decision"] == "WAIT"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("timeframe", ["1D", "4H", "1H"])
def test_exact_supported_timeframe_rows_remain_valid(timeframe: str) -> None:
    report = _report(grounding=_rows("EUR/USD"))
    assert report["bar_evidence"][timeframe]["history_status"] == "ok"
    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["audit"]["passed"]


@pytest.mark.parametrize(
    "providers",
    [
        {"1D": "daily-provider", "4H": "intraday-provider", "1H": "intraday-provider"},
        {"1D": "daily-provider", "4H": "four-hour-provider", "1H": "hourly-provider"},
    ],
    ids=["two_providers", "three_providers"],
)
def test_providers_differing_across_timeframes_fail_provenance(providers: dict[str, str]) -> None:
    grounding = _rows("EUR/USD")
    for row in grounding["EUR/USD"]:
        row["source"] = providers[row["timeframe"]]

    report = _report(grounding=grounding)

    assert report["data_provider"] is None
    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["binding_data_gate"]["reasons"][-1].startswith(
        "MIXED_DATA_PROVIDERS"
    )
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_provider_change_only_in_older_history_fails_provenance() -> None:
    grounding = _rows("EUR/USD")
    historical = next(row for row in grounding["EUR/USD"] if row["timeframe"] == "4H")
    historical["source"] = "historical-provider"

    report = _report(grounding=grounding)

    assert all(
        evidence["source"] == "deterministic-fixture"
        for evidence in report["bar_evidence"].values()
    )
    assert report["data_provider"] is None
    assert report["binding_data_gate"]["reasons"][-1].startswith(
        "MIXED_DATA_PROVIDERS"
    )
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("source", [None, 17], ids=["missing", "non_string"])
def test_invalid_historical_source_fails_existing_provenance_contract(source: object) -> None:
    grounding = _rows("EUR/USD")
    historical = next(row for row in grounding["EUR/USD"] if row["timeframe"] == "1D")
    historical["source"] = source

    report = _report(grounding=grounding)

    assert report["data_provider"] == "deterministic-fixture"
    assert "1D: missing source attribution" in report["binding_data_gate"]["reasons"]
    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_uniform_complete_history_provider_preserves_singular_attribution() -> None:
    grounding = _rows("EUR/USD")
    for row in grounding["EUR/USD"]:
        row["source"] = "uniform-provider"

    report = _report(grounding=grounding)

    assert report["data_provider"] == "uniform-provider"
    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["final_decision"] == "WAIT"
    assert report["audit"]["passed"]
    json.dumps(report, allow_nan=False)


def test_auditor_rejects_forged_single_provider_for_mixed_history() -> None:
    grounding = _rows("EUR/USD")
    historical = next(row for row in grounding["EUR/USD"] if row["timeframe"] == "1H")
    historical["source"] = "historical-provider"
    report = _report(grounding=grounding)
    forged = deepcopy(report)
    forged["data_provider"] = "deterministic-fixture"
    forged["binding_data_gate"] = {"status": "PASS", "reasons": []}
    forged["final_decision"] = "WAIT"

    audit = audit_validation_report(forged, grounding)

    assert not audit["passed"]
    assert {
        "DATA_PROVIDER_MISMATCH", "DATA_GATE_MISMATCH",
        "DATA_GATE_REASONS_MISMATCH",
    } <= {issue["code"] for issue in audit["issues"]}
    json.dumps(audit, allow_nan=False)


def test_deterministic_fixture_cannot_be_labelled_captured() -> None:
    grounding = _rows("EUR/USD")
    report = build_validation_report(
        "EUR/USD",
        grounding,
        run_id="deterministic-label",
        evidence_label="CAPTURED_FIXTURE",
    )
    assert report["evidence_label"] == "SIMULATED"
    assert report["audit"]["passed"]

    forged = deepcopy(report)
    forged["evidence_label"] = "CAPTURED_FIXTURE"
    forged["disclosure"] = (
        "Decision support only; no broker execution or guarantee. "
        "Evidence is labelled CAPTURED_FIXTURE."
    )
    codes = {issue["code"] for issue in audit_validation_report(forged, grounding)["issues"]}
    assert "EVIDENCE_LABEL_SOURCE_MISMATCH" in codes


@pytest.mark.parametrize("field", sorted({
    "schema_version", "evidence_label", "pair", "timeframes", "run_id",
    "data_provider", "capture_time", "bar_evidence", "indicator_evidence",
    "agent_conclusions", "dissent", "binding_data_gate", "binding_risk_gate",
    "confidence", "macro", "proposed_levels", "market_claims",
    "final_decision", "recheck_condition", "disclosure", "audit",
}))
def test_each_top_level_field_is_required(field: str) -> None:
    grounding = _rows("EUR/USD")
    report = deepcopy(_report(grounding=grounding))
    report.pop(field)
    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert "REPORT_FIELDS_MISMATCH" in {issue["code"] for issue in audit["issues"]}
    json.dumps(audit, allow_nan=False)


def test_deeply_nested_json_input_fails_safe_without_serialization_escape() -> None:
    nested: object = None
    for _ in range(1500):
        nested = [nested]
    report = build_validation_report(
        nested, nested, run_id="deep", macro_evidence=nested, proposed_levels=nested
    )
    audit = audit_validation_report({"bar_evidence": nested}, nested)
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert not audit["passed"]
    json.dumps(report, allow_nan=False)
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "CLAIM_FIELDS_MISMATCH"),
        ("extra", "CLAIM_FIELDS_MISMATCH"),
        ("wrong_kind", "UNSUPPORTED_CLAIM_KIND"),
        ("noncanonical_pair", "CLAIM_PAIR_MISMATCH"),
        ("wrong_pair_type", "CLAIM_PAIR_MISMATCH"),
        ("naive_timestamp", "CLAIM_TIMESTAMP_MISMATCH"),
        ("nonfinite", "UNSUPPORTED_NUMERICAL_CLAIM"),
    ],
)
def test_complete_numerical_claim_contract_mutations_fail(
    mutation: str, code: str
) -> None:
    grounding = _rows("EUR/USD")
    report = _report(grounding=grounding)
    claim = {
        "id": "claim-contract",
        "kind": "price",
        "value": report["indicator_evidence"]["1H"]["latest"]["close"],
        "source": "deterministic-fixture",
        "observed_at": report["bar_evidence"]["1H"]["last_bar_at"],
        "evidence_ref": "1H.latest.close",
    }
    if mutation == "missing":
        claim.pop("id")
    elif mutation == "extra":
        claim["authority"] = "fabricated"
    elif mutation == "wrong_kind":
        claim["kind"] = "forecast"
    elif mutation == "noncanonical_pair":
        claim["pair"] = "EURUSD.FX"
    elif mutation == "wrong_pair_type":
        claim["pair"] = ["EUR", "USD"]
    elif mutation == "naive_timestamp":
        claim["observed_at"] = claim["observed_at"].removesuffix("+00:00")
    else:
        claim["value"] = math.inf
    report["market_claims"] = [claim]

    audit = audit_validation_report(report, grounding)
    assert not audit["passed"]
    assert code in {issue["code"] for issue in audit["issues"]}
    json.dumps(audit, allow_nan=False)


def test_naive_bar_timestamp_fails_data_gate() -> None:
    grounding = _rows("EUR/USD")
    grounding["EUR/USD"][0]["trade_date"] = "2025-01-01T00:00:00"
    report = _report(grounding=grounding)
    assert report["binding_data_gate"]["status"] == "FAIL"
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["audit"]["passed"]
