# TradeCoreFX Phase 6–9 integration foundation

## Status

This change provides an engineering integration foundation for deterministic decision support. It does not establish predictive accuracy, profitability, investor readiness, broker execution, or production acceptance. Phases 6–9 remain partially complete until credentialed provider acceptance, an authoritative current-macro integration, long-duration operation, and later release acceptance are completed.

## Architecture and decision flow

Only the `tradecorefx_forex_desk` preset enters this path. The runtime fetches isolated multitimeframe grounding, persists it on `SwarmRun`, and finalizes a `tradecorefx_validation` object from the preset `target`. The LLM final report remains a separate unaudited narrative and cannot set evidence, gates, confidence, audit, or decision.

The deterministic order is: isolate and normalize the pair; validate 1D/4H/1H OHLC, timestamps, freshness, history, and provider consistency; derive indicators; evaluate macro; evaluate grounded direction/levels/risk; independently audit; then emit `NO_TRADE_DATA`, `WAIT`, `LONG_SETUP`, or `SHORT_SETUP`. Missing or unsafe input fails closed. With current macro unavailable, otherwise-valid runtime output is `WAIT` and confidence remains capped.

At every terminal list/detail API read, the report is independently rebuilt from persisted grounding. Missing, malformed, or different stored content produces `integrity_state: FAILED_REBUILT_SAFE`; clients receive the conservative rebuilt report, never the stored directional value. A genuinely pending/running run with no persisted validation remains neutral with null validation/decision and `PENDING` integrity/audit states. Non-TradeCoreFX presets retain their old runtime behavior and use `NOT_APPLICABLE`/null validation metadata.

## API fields

Authenticated `GET /swarm/runs` adds bounded metadata: `is_tradecorefx`, `pair`, `requested_horizon`, `data_provider`, `llm_provider`, `model`, `capture_time`, `evidence_label`, `decision`, `audit_state`, `integrity_state`, `timeframe_summary`, and `completed_at`. It does not return grounding histories or full indicator arrays.

Authenticated `GET /swarm/runs/{run_id}` adds the same metadata and `tradecorefx_validation`. The report contains binding data/risk gates and reasons, confidence total/cap, macro state, dissent, recheck condition, audit, disclosure, and per-timeframe evidence. `final_report` remains present for compatibility but is unaudited narrative. The existing terminal `run_completed` event adds decision/readiness keys without changing its event shape.

## Evidence labels and provider limits

- `SIMULATED`: deterministic/generated fixture evidence.
- `CAPTURED_FIXTURE`: an explicitly captured offline test fixture.
- `CAPTURED_PROVIDER`: assigned only during trusted internal finalization/read reconstruction when complete, consistent attribution identifies the approved public `yfinance` path.

`LIVE` is prohibited. Caller or model labels cannot promote evidence. Mixed, missing, malformed, or unapproved attribution cannot receive the provider-captured label and fails the data gate. Yahoo Finance bars are public research evidence, not executable broker quotes. No keys, environment values, connector secrets, or grounding histories are exposed.

There is deliberately no synthetic macro connector. Macro remains `UNAVAILABLE` until evidence includes an authoritative source, observation timestamp, and HTTPS URL satisfying the existing deterministic contract.

## UI behavior

The authenticated `/market-intelligence` route loads the swarm list/detail APIs, supports refresh and keyboard operation, and has loading, empty, error, pending, and degraded states. A directional state is shown only when integrity, audit, data, risk, macro, and the decision enum all agree; contradictions visibly fall back to `WAIT`, while authoritative `NO_TRADE_DATA` remains visible. It shows exact binding reasons, confidence/cap, macro, audit/integrity, dissent/recheck, separate market-data and LLM providers, and 1D/4H/1H evidence in a responsive table. Missing or invalid timestamp metadata reads `Unavailable`. There are no order controls or performance/accuracy claims.

## Security boundary

The runtime and API derive auditable facts exclusively from persisted isolated grounding. Model prose and caller report fields are untrusted. Swarm authentication is unchanged. Responses are JSON-safe and list payloads are bounded. This path performs no broker operation and exposes no secrets.

## Verification

The implementation is covered by deterministic Phase 6–9 tests for legacy loading, trusted/caller evidence labeling, mixed attribution, macro-bound `WAIT`, missing grounding, strict JSON, tamper reconstruction, bounded summaries, and non-TradeCoreFX compatibility; frontend tests cover data rendering, provider separation, disclosure, refresh, empty/error states, and absence of execution controls.

Verified on 2026-08-15:

- Required unique Batch 1 combined command (the 12 files specified for this integration): **385 passed**.
- Broader relevant Phase 6–9/API/authentication command: **123 passed, 4 pre-existing FastAPI deprecation warnings**.
- `pytest -q tests/test_tradecorefx_phase6_9_integration.py tests/test_swarm_run_metadata.py`: **19 passed**.
- `npm run test:run`: **33 files, 296 tests passed**.
- `npm run build`: TypeScript and Vite production build succeeded; Vite retained its existing large chart-chunk advisory.
- `python -m py_compile src/swarm/models.py src/swarm/tradecorefx_validation.py src/swarm/runtime.py src/api/swarm_routes.py`: succeeded.

## Remaining acceptance work

Credentialed/public-provider acceptance in the target deployment, current authoritative macro evidence, long-duration/staleness and operational testing, broader accessibility review, and later phase release criteria remain outstanding. Directional states cannot occur in the current runtime without authoritative macro plus fully grounded deterministic levels; no production-complete phase claim is made here.
