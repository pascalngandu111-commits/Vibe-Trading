import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.tradecorefx_acceptance as acceptance_script
from scripts.tradecorefx_acceptance import main
from src.swarm.tradecorefx_acceptance import load_provider_fixture, run_bounded_concurrency
from src.swarm.models import RunStatus, SwarmAgentSpec, SwarmRun, SwarmTask, TaskStatus
from src.swarm.store import SwarmStore
from src.swarm.task_store import TaskStore


FIXTURES = Path(__file__).parent / "fixtures" / "tradecorefx_provider_contracts"


@pytest.mark.parametrize("scenario", ["success", "timeout", "throttling", "schema_drift", "partial_timeframe_failure", "mixed_providers", "stale_data"])
def test_recorded_provider_scenarios_are_non_live_and_safe(scenario):
    result = load_provider_fixture(FIXTURES / f"{scenario}.json")
    assert result["live"] is False
    if scenario != "success": assert result["safe_state"] in {"WAIT", "NO_TRADE_DATA"}


def test_bounded_concurrency_reports_pair_isolation_and_redacts_exceptions():
    seen = []
    def runner(pair):
        seen.append(pair)
        if pair == "USD/JPY": raise RuntimeError("secret=must-not-appear")
    report = run_bounded_concurrency(["EUR/USD", "GBP/USD", "USD/JPY"], 2, runner)
    assert sorted(seen) == ["EUR/USD", "GBP/USD", "USD/JPY"]
    assert report["failed_pairs"] == ["USD/JPY"]
    assert "secret" not in json.dumps(report)
    assert report["attempted"] == 3 and report["failed"] == 1
    assert report["evidence_kind"] == "FIXTURE_ONLY"


def test_acceptance_is_disabled_by_default_and_truthfully_labelled(capsys):
    assert main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"status": "SKIPPED", "reason": "soak is disabled; pass --soak explicitly"}


def test_removed_credentialed_mode_fails_explicitly_without_provider_fallback(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--credentialed-provider"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "unrecognized arguments: --credentialed-provider" in captured.err
    assert "FIXTURE" not in captured.out


def test_fixture_soak_is_bounded_and_report_stays_fixture_only(monkeypatch, tmp_path, capsys):
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(acceptance_script.time, "monotonic", lambda: next(ticks))
    report_path = tmp_path / "soak.json"
    assert main([
        "--soak", "--duration-seconds", "1", "--concurrency", "2",
        "--report", str(report_path),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "COMPLETED_FIXTURE_ONLY"
    assert report["fixture_evidence"] is True
    assert report["attempted"] == 4 and report["batches"] == 1
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_bounded_concurrency_normalizes_pairs_and_rejects_cross_pair_aliases():
    seen = []
    report = run_bounded_concurrency(["eurusd", "gbp/usd"], 2, seen.append)
    assert seen == ["EUR/USD", "GBP/USD"] or seen == ["GBP/USD", "EUR/USD"]
    assert report["completed"] == 2
    with pytest.raises(ValueError):
        run_bounded_concurrency(["EUR/USD", "eurusd"], 2, lambda _: None)
    with pytest.raises(ValueError):
        run_bounded_concurrency(["BTC/USD"], 1, lambda _: None)


def test_fixture_loader_rejects_live_or_unbounded_contracts(tmp_path):
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({
        "evidence_kind": "RECORDED_CONTRACT_FIXTURE", "live": True, "scenario": "success",
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        load_provider_fixture(forged)

    oversized = tmp_path / "oversized.json"
    oversized.write_text(" " * 16_385, encoding="utf-8")
    with pytest.raises(ValueError):
        load_provider_fixture(oversized)


@pytest.mark.parametrize(
    ("task_status", "expected_status"),
    [(TaskStatus.completed, RunStatus.completed), (TaskStatus.failed, RunStatus.failed)],
)
def test_interrupted_run_reconciles_after_restart_without_forged_success(
    tmp_path, task_status, expected_status,
):
    agent = SwarmAgentSpec(id="analyst", role="Analyst", system_prompt="bounded")
    task = SwarmTask(id="analysis", agent_id=agent.id, prompt_template="analyze")
    run = SwarmRun(
        id=f"restart-{task_status.value}", preset_name="tradecorefx_forex_desk",
        status=RunStatus.running, user_vars={"target": "EUR/USD"},
        agents=[agent], tasks=[task], created_at=datetime.now(timezone.utc).isoformat(),
    )
    SwarmStore(tmp_path).create_run(run)
    TaskStore(tmp_path / run.id).save_task(task.model_copy(update={
        "status": task_status,
        "summary": "deterministic report" if task_status == TaskStatus.completed else None,
        "error": "redacted provider failure" if task_status == TaskStatus.failed else None,
    }))

    restarted_store = SwarmStore(tmp_path)
    recovered = restarted_store.reconcile_run(restarted_store.load_run(run.id), write=True)
    assert recovered.status == expected_status
    assert restarted_store.load_run(run.id).status == expected_status
    if task_status == TaskStatus.failed:
        assert recovered.final_report is None


@pytest.mark.parametrize(("count", "workers"), [(0, 1), (129, 1), (1, 0), (1, 33)])
def test_harness_limits(count, workers):
    with pytest.raises(ValueError): run_bounded_concurrency(["EUR/USD"] * count, workers, lambda _: None)
