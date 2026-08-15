"""Tests for the TradeCoreFX evidence-first forex desk preset."""

from __future__ import annotations

from src.swarm import presets
from src.tools.swarm_tool import (
    _build_variables,
    _match_preset,
    _normalize_preset_name,
)


def test_tradecorefx_forex_desk_is_valid_three_layer_dag() -> None:
    report = presets.inspect_preset("tradecorefx_forex_desk")

    assert report["valid"], report["errors"]
    assert report["variables"] == ["target", "timeframe"]
    assert report["layers"] == [
        [
            {"task_id": "task-fx-structure", "agent_id": "fx_structure_analyst"},
            {"task_id": "task-fx-momentum", "agent_id": "fx_momentum_regime_analyst"},
            {"task_id": "task-fx-macro", "agent_id": "fx_macro_catalyst_analyst"},
        ],
        [{"task_id": "task-fx-risk", "agent_id": "fx_risk_gatekeeper"}],
        [{"task_id": "task-fx-decision", "agent_id": "chief_fx_strategist"}],
    ]


def test_tradecorefx_forex_desk_has_binding_safety_gates() -> None:
    data = presets.load_preset("tradecorefx_forex_desk")
    prompts = {
        agent["id"]: agent["system_prompt"]
        for agent in data["agents"]
    }

    risk_prompt = prompts["fx_risk_gatekeeper"]
    decision_prompt = prompts["chief_fx_strategist"]
    structure_prompt = prompts["fx_structure_analyst"]

    assert "RISK_GATE=FAIL" in risk_prompt
    assert "Reward-to-risk" in risk_prompt
    assert "cannot override RISK_GATE=FAIL" in decision_prompt
    assert "NO_TRADE_DATA" in decision_prompt
    assert "LONG_SETUP" in decision_prompt
    assert "SHORT_SETUP" in decision_prompt
    assert "Do not claim OBV" not in structure_prompt
    assert "Never claim OBV" in structure_prompt


def test_direct_forex_pair_prompts_route_to_tradecorefx() -> None:
    assert _match_preset("Analyze EUR/USD across 1D, 4H, and 1H") == (
        "tradecorefx_forex_desk"
    )
    assert _match_preset("TradeCoreFX forex setup for USDJPY.FX") == (
        "tradecorefx_forex_desk"
    )
    assert _normalize_preset_name("TradeCoreFX Forex Desk") == (
        "tradecorefx_forex_desk"
    )


def test_tradecorefx_variable_builder_extracts_pair() -> None:
    variables = _build_variables(
        "tradecorefx_forex_desk",
        "Please analyze EUR/USD for a swing setup",
    )

    assert variables == {
        "target": "EUR/USD",
        "timeframe": "multi-timeframe 1D/4H/1H",
    }
