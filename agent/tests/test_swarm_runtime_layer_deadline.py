"""Regression tests for capacity-aware swarm layer deadlines."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import src.swarm.runtime as rt
from src.swarm.models import (
    SwarmAgentSpec,
    SwarmRun,
    SwarmTask,
    TaskStatus,
    WorkerResult,
)
from src.swarm.store import SwarmStore
from src.swarm.task_store import TaskStore


def test_layer_deadline_scales_three_tasks_on_one_worker() -> None:
    assert rt._capacity_aware_layer_deadline(
        submitted_tasks=3,
        effective_workers=1,
        maximum_per_task_budget=900,
        deadline_buffer=60,
    ) == 2760


def test_layer_deadline_keeps_fully_parallel_budget() -> None:
    assert rt._capacity_aware_layer_deadline(
        submitted_tasks=3,
        effective_workers=3,
        maximum_per_task_budget=900,
        deadline_buffer=60,
    ) == 960


def test_layer_deadline_clamps_workers_greater_than_task_count() -> None:
    assert rt._capacity_aware_layer_deadline(
        submitted_tasks=3,
        effective_workers=8,
        maximum_per_task_budget=900,
        deadline_buffer=60,
    ) == 960


def test_layer_deadline_returns_none_for_zero_submitted_tasks() -> None:
    assert rt._capacity_aware_layer_deadline(
        submitted_tasks=0,
        effective_workers=1,
        maximum_per_task_budget=900,
        deadline_buffer=60,
    ) is None


def test_layer_deadline_returns_none_for_zero_budget() -> None:
    assert rt._capacity_aware_layer_deadline(
        submitted_tasks=3,
        effective_workers=1,
        maximum_per_task_budget=0,
        deadline_buffer=60,
    ) is None


def test_layer_deadline_clamps_zero_workers_to_one() -> None:
    assert rt._capacity_aware_layer_deadline(
        submitted_tasks=5,
        effective_workers=0,
        maximum_per_task_budget=100,
        deadline_buffer=60,
    ) == 560


def test_blocked_tasks_do_not_inflate_submitted_task_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    store = SwarmStore(base_dir=tmp_path)
    runtime = rt.SwarmRuntime(store=store, max_workers=1)
    agents = [
        SwarmAgentSpec(
            id="analyst",
            role="Analyst",
            system_prompt="x",
            timeout_seconds=900,
            max_retries=0,
        ),
        SwarmAgentSpec(
            id="blocked",
            role="Blocked",
            system_prompt="x",
            timeout_seconds=900,
            max_retries=0,
        ),
    ]
    tasks = [
        SwarmTask(id="ready", agent_id="analyst", prompt_template="do ready"),
        SwarmTask(
            id="blocked",
            agent_id="blocked",
            prompt_template="do blocked",
            depends_on=["missing"],
        ),
    ]
    run = SwarmRun(
        id="r-deadline",
        preset_name="demo",
        created_at=datetime.now(timezone.utc).isoformat(),
        agents=agents,
        tasks=tasks,
    )
    store.create_run(run)
    task_store = TaskStore(store.run_dir(run.id))
    for task in tasks:
        task_store.save_task(task)

    captured: dict[str, object] = {}

    def fake_as_completed(futures, timeout=None):
        captured["submitted"] = len(futures)
        captured["timeout"] = timeout
        return iter(())

    monkeypatch.setattr(
        rt,
        "run_worker",
        lambda *args, **kwargs: WorkerResult(status="completed", summary="done"),
    )
    monkeypatch.setattr(rt, "as_completed", fake_as_completed)

    results = runtime._execute_layer(
        run=run,
        task_store=task_store,
        agent_map={agent.id: agent for agent in agents},
        layer_task_ids=["ready", "blocked"],
        task_summaries={},
        run_dir=store.run_dir(run.id),
        cancel_event=threading.Event(),
    )

    assert captured == {"submitted": 1, "timeout": 960}
    assert set(results) == set()
    assert task_store.load_task("blocked").status == TaskStatus.blocked
