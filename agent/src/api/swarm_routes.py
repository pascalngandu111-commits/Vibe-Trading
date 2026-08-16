"""Swarm HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_swarm_routes(app, ...)``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import StreamingResponse

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_swarm_runtime = None
TRADECOREFX_PRESET = "tradecorefx_forex_desk"
TRADECOREFX_HORIZON = "multi-timeframe 1D/4H/1H"


class TradeCoreFXRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    pair: str = Field(min_length=1, max_length=32)


def _tradecorefx_view(run) -> tuple[dict | None, str]:
    """Rebuild the binding report at every read boundary."""
    if run.preset_name != TRADECOREFX_PRESET:
        return None, "NOT_APPLICABLE"
    status = getattr(run.status, "value", run.status)
    if status in {"pending", "running"} and run.tradecorefx_validation is None:
        return None, "PENDING"
    from src.swarm.tradecorefx_validation import build_captured_provider_validation_report

    rebuilt = build_captured_provider_validation_report(
        run.user_vars.get("target"), run.grounding_data or {}, run_id=run.id,
    )
    try:
        intact = json.dumps(run.tradecorefx_validation, sort_keys=True, allow_nan=False) == json.dumps(
            rebuilt, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError, OverflowError):
        intact = False
    return rebuilt, "VERIFIED" if intact else "FAILED_REBUILT_SAFE"


def _tradecorefx_summary(run, report: dict | None, integrity: str) -> dict:
    bars = report.get("bar_evidence", {}) if report else {}
    applicable = run.preset_name == TRADECOREFX_PRESET
    audit_state = (
        "NOT_APPLICABLE"
        if not applicable
        else "PENDING"
        if integrity == "PENDING"
        else "PASSED"
        if report and report.get("audit", {}).get("passed")
        else "FAILED"
    )
    return {
        "is_tradecorefx": applicable,
        "pair": report.get("pair") if report else None,
        "requested_horizon": run.user_vars.get("horizon") if run.preset_name == TRADECOREFX_PRESET else None,
        "data_provider": report.get("data_provider") if report else None,
        "llm_provider": run.provider,
        "model": run.model,
        "capture_time": report.get("capture_time") if report else None,
        "evidence_label": report.get("evidence_label") if report else None,
        "decision": report.get("final_decision") if report else None,
        "audit_state": audit_state,
        "integrity_state": integrity,
        "timeframe_summary": {
            timeframe: {
                key: evidence.get(key)
                for key in ("last_bar_at", "freshness", "history_status", "bars")
            }
            for timeframe, evidence in bars.items()
            if timeframe in {"1D", "4H", "1H"} and isinstance(evidence, dict)
        },
    }


def _get_swarm_runtime():
    """Lazy-init SwarmRuntime singleton."""
    global _swarm_runtime
    if _swarm_runtime is not None:
        return _swarm_runtime
    from src.config import load_swarm_agent_config
    from src.config.accessor import get_env_config
    from src.swarm.store import SwarmStore
    from src.swarm.runtime import SwarmRuntime

    # Adjust path: this file is at agent/src/api/, so parent.parent.parent = agent/
    swarm_dir = Path(__file__).resolve().parent.parent.parent / ".swarm" / "runs"
    store = SwarmStore(base_dir=swarm_dir)
    # Boot-time / operator-trusted: REST API callers cannot influence the
    # config path. See docs/2026-05-25_swarm_mcp_tools_roadmap.md.
    agent_config = load_swarm_agent_config()
    _swarm_runtime = SwarmRuntime(
        store=store,
        max_workers=get_env_config().swarm.swarm_max_workers,
        agent_config=agent_config,
    )
    return _swarm_runtime


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_swarm_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    require_event_stream_auth: AuthDep | None = None,
) -> None:
    """Mount the swarm routes onto ``app``.

    Resolves ``require_auth`` and ``require_event_stream_auth`` from the host
    ``api_server`` module via ``sys.modules`` when not passed explicitly.
    """
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    if host is None:
        raise RuntimeError(
            "register_swarm_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth
    if require_event_stream_auth is None:
        require_event_stream_auth = host.require_event_stream_auth

    def _host_validate_path_param(value: str, kind: str) -> None:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        h._validate_path_param(value, kind)

    def _host_shell_tools_enabled_for_request(request: Request) -> bool:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        return h._shell_tools_enabled_for_request(request)

    # --- Routes ---

    @app.get("/swarm/presets")
    async def list_swarm_presets():
        """List Swarm YAML presets."""
        from src.swarm.presets import list_presets

        return list_presets()

    @app.post("/swarm/runs", dependencies=[Depends(require_auth)])
    async def create_swarm_run(payload: dict, http_request: Request):
        """Start a swarm run: body must include preset_name and user_vars."""
        runtime = _get_swarm_runtime()
        preset_name = payload.get("preset_name", "")
        user_vars = payload.get("user_vars", {})
        try:
            run = runtime.start_run(
                preset_name,
                user_vars,
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
            )
            return {"id": run.id, "status": run.status.value, "preset_name": run.preset_name}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/market-intelligence/runs", dependencies=[Depends(require_auth)])
    async def create_tradecorefx_run(payload: TradeCoreFXRunRequest, http_request: Request):
        """Start the fixed TradeCoreFX beta workflow from a canonical FX pair."""
        from src.swarm.tradecorefx_validation import normalize_forex_pair

        if http_request.query_params:
            raise HTTPException(status_code=422, detail="Query parameters are not accepted")
        pair = normalize_forex_pair(payload.pair)
        if pair is None:
            raise HTTPException(status_code=422, detail="Enter a supported beta FX pair")
        runtime = _get_swarm_runtime()
        try:
            run = runtime.start_run(
                TRADECOREFX_PRESET,
                {"target": pair, "horizon": TRADECOREFX_HORIZON},
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
            )
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=503, detail="Market analysis could not be started")
        return {
            "id": run.id,
            "pair": pair,
            "requested_horizon": TRADECOREFX_HORIZON,
            "status": run.status.value,
            "navigation": {"detail_path": f"/market-intelligence?run={run.id}"},
        }

    @app.get("/swarm/runs", dependencies=[Depends(require_auth)])
    async def list_swarm_runs(
        limit: int = Query(20, ge=1, le=100),
        preset_name: str | None = Query(None),
    ):
        """List swarm runs (newest first), reconciled."""
        runtime = _get_swarm_runtime()
        runs = runtime._store.list_runs(limit=limit, preset_name=preset_name)
        items = []
        for r in runs:
            # Reconcile each row: a zombie running run will be auto-finalized so
            # the dashboard never shows a "running" stuck row.
            reconciled = runtime._store.reconcile_run(r, write=True)
            validation, integrity = _tradecorefx_view(reconciled)
            items.append(
                {
                    "id": reconciled.id,
                    "preset_name": reconciled.preset_name,
                    "status": reconciled.status.value,
                    "is_stale": runtime._store.is_run_stale(reconciled),
                    "created_at": reconciled.created_at,
                    "completed_at": reconciled.completed_at,
                    "task_count": len(reconciled.tasks),
                    "completed_count": sum(
                        1 for t in reconciled.tasks if t.status.value == "completed"
                    ),
                    **_tradecorefx_summary(reconciled, validation, integrity),
                }
            )
        return items

    @app.get("/swarm/runs/{run_id}", dependencies=[Depends(require_auth)])
    async def get_swarm_run(run_id: str):
        """Swarm run detail including task statuses (reconciled)."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        loaded = runtime._store.load_run(run_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        run = runtime._store.reconcile_run(loaded, write=True)
        validation, integrity = _tradecorefx_view(run)

        return {
            "id": run.id,
            "preset_name": run.preset_name,
            "status": run.status.value,
            "is_stale": runtime._store.is_run_stale(run),
            "user_vars": run.user_vars,
            "agents": [a.model_dump() for a in run.agents],
            "tasks": [t.model_dump() for t in run.tasks],
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "final_report": run.final_report,
            "tradecorefx_validation": validation,
            **_tradecorefx_summary(run, validation, integrity),
        }

    @app.get(
        "/swarm/runs/{run_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def swarm_run_events(
        run_id: str, request: Request, last_index: int = Query(0, ge=0)
    ):
        """SSE stream for a swarm run."""
        import asyncio

        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()

        async def event_stream():
            idx = last_index
            while True:
                if await request.is_disconnected():
                    break
                events = runtime._store.read_events(run_id, after_index=idx)
                for evt in events:
                    idx += 1
                    yield f"id: {idx}\nevent: {evt.type}\ndata: {json.dumps(evt.model_dump(), ensure_ascii=False)}\n\n"
                run = runtime._store.load_run(run_id)
                if run:
                    # Reconcile so a zombie running run can still close this SSE
                    # stream cleanly — without it, a dead host would keep the
                    # stream open forever and block the dashboard's "done" state.
                    reconciled = runtime._store.reconcile_run(run, write=True)
                    if reconciled.status.value in ("completed", "failed", "cancelled"):
                        yield f"event: done\ndata: {{\"status\": \"{reconciled.status.value}\"}}\n\n"
                        break
                await asyncio.sleep(2)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/swarm/runs/{run_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_swarm_run(run_id: str):
        """Cancel an active swarm run."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        ok = runtime.cancel_run(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"No active run {run_id}")
        return {"status": "cancelled"}

    @app.post("/swarm/runs/{run_id}/retry", dependencies=[Depends(require_auth)])
    async def retry_swarm_run(run_id: str, http_request: Request):
        """Retry a failed, stale, or cancelled swarm run.

        Creates a new run with the same preset and user_vars as the original.
        """
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        loaded = runtime._store.load_run(run_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        # Reconcile first so a stale "running" run whose host died gets demoted
        # before we gate on status; only a genuinely active run blocks retry.
        from src.swarm.models import RunStatus

        reconciled = runtime._store.reconcile_run(loaded, write=True)
        if reconciled.status == RunStatus.running:
            raise HTTPException(
                status_code=409, detail="Cannot retry a running run. Cancel it first."
            )

        try:
            new_run = runtime.start_run(
                reconciled.preset_name,
                reconciled.user_vars or {},
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
            )
            return {
                "id": new_run.id,
                "status": new_run.status.value,
                "preset_name": new_run.preset_name,
            }
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
