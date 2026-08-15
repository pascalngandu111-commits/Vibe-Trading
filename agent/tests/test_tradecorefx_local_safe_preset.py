"""Regression guard for the local-safe TradeCoreFX Forex Desk profile."""

from __future__ import annotations

from src.swarm import presets


def test_tradecorefx_local_safe_agents_are_toolless_and_bounded() -> None:
    """The CPU-local profile must not expose tools or unbounded retry loops."""
    data = presets.load_preset("tradecorefx_forex_desk")
    agents = {agent["id"]: agent for agent in data["agents"]}

    assert set(agents) == {
        "fx_structure_analyst",
        "fx_momentum_regime_analyst",
        "fx_macro_catalyst_analyst",
        "fx_risk_gatekeeper",
        "chief_fx_strategist",
    }

    for agent_id, agent in agents.items():
        assert agent["tools"] == [], f"{agent_id} unexpectedly exposes tools"
        assert agent["skills"] == [], f"{agent_id} unexpectedly loads skills"
        assert agent["max_retries"] == 0, f"{agent_id} unexpectedly retries"
        assert 1 <= agent["max_iterations"] <= 4, (
            f"{agent_id} exceeds the local-safe iteration budget"
        )


def test_tradecorefx_local_macro_remains_explicitly_unavailable() -> None:
    """Tool-less local inference must never manufacture current macro facts."""
    data = presets.load_preset("tradecorefx_forex_desk")
    macro = next(
        agent for agent in data["agents"]
        if agent["id"] == "fx_macro_catalyst_analyst"
    )

    prompt = macro["system_prompt"]
    assert "CURRENT_MACRO: UNAVAILABLE" in prompt
    assert "Training-memory claims are prohibited" in prompt
    assert "VERIFIED_FACTS: none" in prompt
    assert "VERIFIED_CATALYSTS: none" in prompt