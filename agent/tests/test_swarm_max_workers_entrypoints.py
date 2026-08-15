"""Regression tests for swarm worker limits at CLI and API entrypoints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _config(max_workers: int) -> SimpleNamespace:
    """Return the minimum config shape used by swarm entrypoints."""
    return SimpleNamespace(
        swarm=SimpleNamespace(swarm_max_workers=max_workers),
    )


def test_cli_swarm_run_forwards_configured_max_workers(monkeypatch) -> None:
    """Direct ``--swarm-run`` execution must honor SWARM_MAX_WORKERS."""
    import cli._legacy as legacy
    import src.config as config_module
    import src.swarm.runtime as runtime_module
    import src.swarm.store as store_module

    captured: dict[str, object] = {}

    class ConstructionObserved(Exception):
        """Stop the command after recording runtime construction."""

    class FakeRuntime:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)
            raise ConstructionObserved

    monkeypatch.setattr(legacy, "get_env_config", lambda: _config(1))
    monkeypatch.setattr(config_module, "load_swarm_agent_config", lambda: object())
    monkeypatch.setattr(store_module, "SwarmStore", lambda base_dir: object())
    monkeypatch.setattr(runtime_module, "SwarmRuntime", FakeRuntime)

    with pytest.raises(ConstructionObserved):
        legacy.cmd_swarm_run_live("demo", '{"goal":"EUR/USD"}')

    assert captured["max_workers"] == 1
    assert "agent_config" in captured


def test_api_swarm_runtime_forwards_configured_max_workers(monkeypatch) -> None:
    """Web/API swarm execution must honor SWARM_MAX_WORKERS."""
    import src.api.swarm_routes as routes
    import src.config as config_module
    import src.config.accessor as accessor_module
    import src.swarm.runtime as runtime_module
    import src.swarm.store as store_module

    captured: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(accessor_module, "get_env_config", lambda: _config(2))
    monkeypatch.setattr(config_module, "load_swarm_agent_config", lambda: object())
    monkeypatch.setattr(store_module, "SwarmStore", lambda base_dir: object())
    monkeypatch.setattr(runtime_module, "SwarmRuntime", FakeRuntime)
    monkeypatch.setattr(routes, "_swarm_runtime", None)

    runtime = routes._get_swarm_runtime()

    assert isinstance(runtime, FakeRuntime)
    assert captured["max_workers"] == 2
    assert "agent_config" in captured
    routes._swarm_runtime = None
