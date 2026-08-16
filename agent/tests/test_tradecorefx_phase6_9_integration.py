from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from src.api.swarm_routes import _tradecorefx_summary, _tradecorefx_view
from src.swarm.models import SwarmRun
from src.swarm.tradecorefx_validation import (
    audit_validation_report,
    build_captured_provider_validation_report,
    build_validation_report,
    _build_trusted_macro_validation_report,
)
from src.swarm.tradecorefx_macro import MacroEvidence, TrustedMacroRegistry


def _grounding(source: str = "yfinance") -> dict[str, list[dict]]:
    rows: list[dict] = []
    for timeframe, frequency in (("1D", "D"), ("4H", "4h"), ("1H", "h")):
        for index, timestamp in enumerate(pd.date_range("2025-01-01", periods=220, freq=frequency, tz="UTC")):
            close = 1.05 + index * .0001
            rows.append({
                "trade_date": timestamp.isoformat(), "open": close - .00005,
                "high": close + .0001, "low": close - .0001, "close": close,
                "volume": 0, "timeframe": timeframe, "source": source,
                "fetched_at": "2026-08-14T12:00:00+00:00", "market": "forex",
                "is_stale": False,
            })
    return {"EUR/USD": rows}


def _run(report=None, grounding=None) -> SwarmRun:
    return SwarmRun(
        id="phase-6-9", preset_name="tradecorefx_forex_desk",
        user_vars={"target": "EUR/USD", "horizon": "swing"},
        created_at=datetime.now(timezone.utc).isoformat(), provider="openai",
        model="test-model", grounding_data=grounding if grounding is not None else _grounding(),
        tradecorefx_validation=report,
    )


def test_legacy_run_without_validation_loads() -> None:
    run = SwarmRun.model_validate_json('{"id":"old","preset_name":"old","created_at":"2025-01-01T00:00:00Z"}')
    assert run.tradecorefx_validation is None


def test_trusted_capture_gets_honest_public_provider_label() -> None:
    report = build_captured_provider_validation_report("EUR/USD", _grounding(), run_id="r")
    assert report["evidence_label"] == "CAPTURED_PROVIDER"
    assert report["data_provider"] == "yfinance"
    assert report["final_decision"] == "WAIT"
    assert report["macro"]["status"] == "UNAVAILABLE"
    assert report["audit"]["passed"]


def test_public_auditor_rejects_self_authorized_provider_capture() -> None:
    report = build_validation_report("EUR/USD", _grounding(), run_id="r")
    report["evidence_label"] = "CAPTURED_PROVIDER"
    report["disclosure"] = report["disclosure"].replace("SIMULATED", "CAPTURED_PROVIDER")
    audit = audit_validation_report(report, _grounding())
    assert not audit["passed"]
    assert "EVIDENCE_LABEL_SOURCE_MISMATCH" in {issue["code"] for issue in audit["issues"]}


def test_caller_cannot_forge_live_or_captured_provider() -> None:
    assert build_validation_report("EUR/USD", _grounding(), run_id="r", evidence_label="LIVE")["evidence_label"] == "SIMULATED"
    assert build_validation_report("EUR/USD", _grounding(), run_id="r", evidence_label="CAPTURED_PROVIDER")["evidence_label"] == "SIMULATED"


def test_public_macro_dictionary_and_forged_trust_state_cannot_verify() -> None:
    forged = {
        "claim": "forged", "source": "caller", "observed_at": "2026-08-14T11:00:00Z",
        "url": "https://macro.example.test/release", "trust_state": "TRUSTED_INTERNAL",
    }
    report = build_validation_report(
        "EUR/USD", _grounding(), run_id="r", macro_evidence=forged
    )
    assert report["macro"]["status"] == "UNAVAILABLE"
    assert report["final_decision"] == "WAIT"
    assert report["confidence"]["cap"] == 50


def test_registry_authorized_macro_only_passes_internal_pair_bound_path() -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    registry = TrustedMacroRegistry(("macro.example.test",), now=lambda: now)
    authorized = registry.authorize(MacroEvidence(
        claim="authorized", source_identity="connector", observed_at="2026-08-14T11:00:00Z",
        retrieved_at="2026-08-14T11:01:00Z", source_url="https://macro.example.test/release",
        applicability="EUR/USD", provenance="INTERNAL_CONNECTOR",
    ))
    public = build_validation_report("EUR/USD", _grounding(), run_id="r", macro_evidence=authorized)
    internal = _build_trusted_macro_validation_report(
        "EUR/USD", _grounding(), run_id="r", registry=registry, authorized_macro=authorized
    )
    wrong_pair = _build_trusted_macro_validation_report(
        "GBP/USD", _grounding(), run_id="r", registry=registry, authorized_macro=authorized
    )
    assert public["macro"]["status"] == "UNAVAILABLE"
    assert internal["macro"]["status"] == "VERIFIED"
    assert wrong_pair["macro"]["status"] == "UNAVAILABLE"


def test_fixture_remains_simulated_and_mixed_provider_fails_closed() -> None:
    fixture = build_captured_provider_validation_report("EUR/USD", _grounding("deterministic-fixture"), run_id="r")
    assert fixture["evidence_label"] == "SIMULATED"
    mixed = _grounding()
    mixed["EUR/USD"][0]["source"] = "other"
    report = build_captured_provider_validation_report("EUR/USD", mixed, run_id="r")
    assert report["final_decision"] == "NO_TRADE_DATA"
    assert report["evidence_label"] == "SIMULATED"


def test_unapproved_provider_cannot_receive_provider_capture_label() -> None:
    report = build_captured_provider_validation_report(
        "EUR/USD", _grounding("unapproved-provider"), run_id="r"
    )
    assert report["binding_data_gate"]["status"] == "PASS"
    assert report["evidence_label"] == "SIMULATED"
    assert report["audit"]["passed"]


def test_provider_capture_requires_every_timeframe() -> None:
    grounding = _grounding()
    grounding["EUR/USD"] = [row for row in grounding["EUR/USD"] if row["timeframe"] != "4H"]
    report = build_captured_provider_validation_report("EUR/USD", grounding, run_id="r")
    assert report["evidence_label"] == "SIMULATED"
    assert report["binding_data_gate"]["status"] == "FAIL"


def test_provider_capture_rejects_malformed_latest_bar() -> None:
    grounding = _grounding()
    latest = max(
        (row for row in grounding["EUR/USD"] if row["timeframe"] == "1H"),
        key=lambda row: row["trade_date"],
    )
    latest["high"] = "malformed"
    report = build_captured_provider_validation_report("EUR/USD", grounding, run_id="r")
    assert report["evidence_label"] == "SIMULATED"
    assert report["binding_data_gate"]["status"] == "FAIL"


def test_read_boundary_detects_tampering_and_rebuilds_safe_state() -> None:
    expected = build_captured_provider_validation_report("EUR/USD", _grounding(), run_id="phase-6-9")
    forged = deepcopy(expected); forged["final_decision"] = "LONG_SETUP"
    rebuilt, integrity = _tradecorefx_view(_run(forged))
    assert integrity == "FAILED_REBUILT_SAFE"
    assert rebuilt["final_decision"] == "WAIT"
    summary = _tradecorefx_summary(_run(forged), rebuilt, integrity)
    assert summary["decision"] == "WAIT"
    assert "indicator_evidence" not in summary
    assert set(summary["timeframe_summary"]) == {"1D", "4H", "1H"}


def test_missing_grounding_is_safe_and_strict_json_serializable() -> None:
    import json
    run = _run(None, {})
    run.status = "completed"
    report, integrity = _tradecorefx_view(run)
    assert integrity == "FAILED_REBUILT_SAFE"
    assert report["final_decision"] == "NO_TRADE_DATA"
    json.dumps(report, allow_nan=False)


def test_pending_run_without_validation_is_neutral() -> None:
    run = _run(None)
    run.status = "running"
    report, integrity = _tradecorefx_view(run)
    summary = _tradecorefx_summary(run, report, integrity)
    assert report is None
    assert summary["integrity_state"] == "PENDING"
    assert summary["audit_state"] == "PENDING"
    assert summary["decision"] is None


def test_terminal_missing_report_rebuilds_safe() -> None:
    run = _run(None)
    run.status = "completed"
    report, integrity = _tradecorefx_view(run)
    assert integrity == "FAILED_REBUILT_SAFE"
    assert report is not None and report["audit"]["passed"]


def test_non_tradecorefx_read_contract_is_unchanged() -> None:
    run = SimpleNamespace(preset_name="other", user_vars={}, provider=None, model=None)
    assert _tradecorefx_view(run) == (None, "NOT_APPLICABLE")
    summary = _tradecorefx_summary(run, None, "NOT_APPLICABLE")
    assert summary["audit_state"] == "NOT_APPLICABLE"
    assert summary["integrity_state"] == "NOT_APPLICABLE"
    assert summary["decision"] is None
