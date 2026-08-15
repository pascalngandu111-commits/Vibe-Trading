from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import api_server
from src.api import swarm_routes
from src.swarm.models import SwarmRun
from src.swarm.store import SwarmStore


def _persist(store: SwarmStore, run_id: str, preset_name: str, created_at: datetime) -> None:
    store.create_run(SwarmRun(
        id=run_id,
        preset_name=preset_name,
        created_at=created_at.isoformat(),
    ))


def test_preset_filter_is_applied_before_limit(tmp_path) -> None:
    store = SwarmStore(tmp_path / "runs")
    oldest = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _persist(store, "tradecorefx-old", "tradecorefx_forex_desk", oldest)
    for index in range(21):
        _persist(store, f"newer-{index}", "other_preset", oldest + timedelta(minutes=index + 1))

    filtered = store.list_runs(limit=20, preset_name="tradecorefx_forex_desk")

    assert [run.id for run in filtered] == ["tradecorefx-old"]


def test_unfiltered_list_retains_newest_first_limit_behavior(tmp_path) -> None:
    store = SwarmStore(tmp_path / "runs")
    oldest = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _persist(store, "oldest", "tradecorefx_forex_desk", oldest)
    for index in range(21):
        _persist(store, f"newer-{index}", "other_preset", oldest + timedelta(minutes=index + 1))

    unfiltered = store.list_runs(limit=20)

    assert len(unfiltered) == 20
    assert unfiltered[0].id == "newer-20"
    assert unfiltered[-1].id == "newer-1"
    assert "oldest" not in {run.id for run in unfiltered}


def test_filtered_endpoint_forwards_exact_preset_before_store_limit(monkeypatch) -> None:
    calls: list[tuple[int, str | None]] = []

    class RecordingStore:
        def list_runs(self, limit: int, preset_name: str | None = None):
            calls.append((limit, preset_name))
            return []

    monkeypatch.setattr(
        swarm_routes,
        "_swarm_runtime",
        SimpleNamespace(_store=RecordingStore()),
    )

    response = TestClient(api_server.app, client=("127.0.0.1", 50000)).get(
        "/swarm/runs?limit=7&preset_name=tradecorefx_forex_desk"
    )

    assert response.status_code == 200
    assert response.json() == []
    assert calls == [(7, "tradecorefx_forex_desk")]


def test_unfiltered_endpoint_retains_default_limit_and_contract(monkeypatch) -> None:
    calls: list[tuple[int, str | None]] = []

    class RecordingStore:
        def list_runs(self, limit: int, preset_name: str | None = None):
            calls.append((limit, preset_name))
            return []

    monkeypatch.setattr(
        swarm_routes,
        "_swarm_runtime",
        SimpleNamespace(_store=RecordingStore()),
    )

    response = TestClient(api_server.app, client=("127.0.0.1", 50000)).get("/swarm/runs")

    assert response.status_code == 200
    assert response.json() == []
    assert calls == [(20, None)]
