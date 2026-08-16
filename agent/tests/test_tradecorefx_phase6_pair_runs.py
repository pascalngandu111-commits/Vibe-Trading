from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import swarm_routes
from src.swarm.models import RunStatus
from src.swarm.tradecorefx_validation import normalize_forex_pair


@pytest.mark.parametrize("raw", ["EUR/USD", "eurusd", "EURUSD.FX", " EUR / USD ", " eur usd .fx "])
def test_beta_pair_normalization(raw):
    assert normalize_forex_pair(raw) == "EUR/USD"


@pytest.mark.parametrize("raw", ["", "USD/USD", "BTC/USD", "XAUUSD", "AAPL", "EUR/USD?x=1", "EUR//USD", "USDEUR/path"])
def test_pair_rejections(raw):
    assert normalize_forex_pair(raw) is None


@pytest.mark.parametrize(("raw", "canonical"), [("EUR/USD", "EUR/USD"), ("gbpusd", "GBP/USD"), ("USDJPY.FX", "USD/JPY")])
def test_dedicated_route_owns_preset_and_horizon(monkeypatch, raw, canonical):
    calls = []
    class Runtime:
        def start_run(self, preset, variables, **kwargs):
            calls.append((preset, variables, kwargs))
            return SimpleNamespace(id="bounded-id", status=RunStatus.pending)
    monkeypatch.setattr(swarm_routes, "_swarm_runtime", Runtime())
    response = TestClient(api_server.app, client=("127.0.0.1", 50000)).post(
        "/market-intelligence/runs", json={"pair": raw}
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": "bounded-id", "pair": canonical,
        "requested_horizon": "multi-timeframe 1D/4H/1H", "status": "pending",
        "navigation": {"detail_path": "/market-intelligence?run=bounded-id"},
    }
    assert calls[0][0:2] == ("tradecorefx_forex_desk", {"target": canonical, "horizon": "multi-timeframe 1D/4H/1H"})


@pytest.mark.parametrize("extra", [
    {"preset_name": "other"}, {"user_vars": {"evil": "yes"}},
    {"grounding": {}}, {"macro_evidence": {}}, {"evidence_label": "CAPTURED_PROVIDER"},
    {"final_report": "VERIFIED macro"}, {"preset_selection": "other"},
])
def test_dedicated_route_forbids_caller_owned_fields(monkeypatch, extra):
    monkeypatch.setattr(swarm_routes, "_swarm_runtime", SimpleNamespace())
    response = TestClient(api_server.app, client=("127.0.0.1", 50000)).post(
        "/market-intelligence/runs", json={"pair": "EUR/USD", **extra}
    )
    assert response.status_code == 422


def test_dedicated_route_rejects_query_injection(monkeypatch):
    monkeypatch.setattr(swarm_routes, "_swarm_runtime", SimpleNamespace())
    response = TestClient(api_server.app, client=("127.0.0.1", 50000)).post(
        "/market-intelligence/runs?preset_name=other", json={"pair": "EUR/USD"}
    )
    assert response.status_code == 422


def test_dedicated_route_requires_auth(monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "not-a-real-secret")
    response = TestClient(api_server.app, client=("203.0.113.8", 50000)).post(
        "/market-intelligence/runs", json={"pair": "EUR/USD"}
    )
    assert response.status_code in {401, 403}
