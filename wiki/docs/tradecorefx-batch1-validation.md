# TradeCoreFX Investor-Validation Beta — Batch 1

Date: 2026-08-15

This Batch 1 covers Phases 1–5 and includes early Phase 6 safe-state
groundwork; it does not claim that Phase 6 is complete. TradeCoreFX remains a
decision-support system. It does not execute orders, connect this workflow to
a broker, or make profit or accuracy guarantees.

## Implemented contracts

- The existing five-agent DAG remains intact: market structure,
  momentum/regime, macro/catalyst, independent risk gatekeeper, and chief FX
  strategist.
- `tradecorefx_validation.py` builds a JSON-safe report containing the pair,
  1D/4H/1H scope, run identifier, provider, capture and bar timestamps,
  freshness, history status, all five agent conclusions, dissent, binding data
  and risk gates, confidence components, final decision, recheck condition,
  evidence label, and disclosure.
- Both public report functions fail safely for arbitrary JSON-compatible input,
  including nulls, booleans, strings, arrays, objects, malformed timestamps and
  URLs, non-finite floats, and overflowing integers. Unsafe evidence produces a
  strict-JSON-safe safe-state report or failed audit rather than an exception.
- The schema version and complete top-level field set are exact. Pair,
  timeframes, run ID, evidence metadata, conclusions, dissent, both gates,
  confidence, macro, decision, recheck condition, and disclosure are
  independently checked with machine-readable mismatch codes.
- Batch 1 evidence labels are restricted to `SIMULATED` and
  `CAPTURED_FIXTURE`; `LIVE` is forbidden in this deterministic workflow.
  Invalid labels are conservatively emitted as `SIMULATED`. Disclosure is
  deterministically bound to the label and retains the no-execution and
  no-guarantee language.
- Decisions are restricted to `LONG_SETUP`, `SHORT_SETUP`, `WAIT`, and
  `NO_TRADE_DATA`.
- A failed data gate binds to `NO_TRADE_DATA`. A passed data gate with a failed
  risk gate binds to `WAIT`. Both gate statuses must be exactly `PASS` or
  `FAIL`; absent, blank, malformed, and unknown statuses fail the audit, and a
  directional decision requires both gates to pass. The auditor independently
  rebuilds both gates from the injected grounding and emits machine-readable
  mismatch/override findings when caller-reported gate state disagrees. It
  also derives the only valid final decision for every gate-state and direction
  combination and emits `FINAL_DECISION_MISMATCH` for any disagreement.
- Actionable setups require finite entry, stop, and target values, finite
  calculated risk/reward and reward-to-risk, and explicit references that
  numerically match the isolated pair's injected or calculated evidence.
  Missing, malformed, unsupported, cross-pair, or mismatched references bind
  the risk gate to `FAIL` and the decision to `WAIT`. Rejected non-finite
  values are sanitized so reports remain strict JSON serializable.
  The auditor independently derives `reward_to_risk` and emits
  `REWARD_TO_RISK_MISMATCH` for missing, fabricated, non-finite, unexpected,
  or otherwise mismatched reported values.
  The stored proposed-level object is limited to direction, the three levels,
  and their three references; unsupported fields are discarded and unsafe
  values are normalized to strict-JSON-safe rejected values before gates and
  audit reasons are derived.
- The local-safe profile retains current macro as `UNAVAILABLE` unless its
  claim and source are non-empty strings, its observation time is a parseable
  timezone-aware ISO-8601 string, and its URL is absolute HTTPS with a valid
  hostname. The shared build/audit check also requires an independently
  derived valid evidence capture time and requires macro `observed_at` to be
  at or before that capture time and no more than 24 hours older. It never uses
  the wall clock or trusts a reported `capture_time` during audit. Stale,
  future, malformed, or unverifiable macro evidence remains `UNAVAILABLE`
  during building; a caller-reported `VERIFIED` value produces
  `UNVERIFIED_MACRO`, retains the 50-point cap, and cannot enable a directional
  setup. This is a conservative deterministic Batch 1 beta policy, not a claim
  that all macro events have the same market relevance.
  Both macro states have exactly five fields. `UNAVAILABLE` requires four null
  content fields; `VERIFIED` rejects missing or additional fields and requires
  internally consistent non-empty content, an aware timestamp in the closed
  capture-relative 24-hour window, and an absolute HTTPS URL.
- Bar source, capture time, last-bar time, freshness, and count are derived
  only from the same normalized, valid OHLC rows used for indicator snapshots.
  Every entry in the selected pair collection is retained through validation;
  nulls, booleans, numbers, strings, arrays, and other non-object entries emit
  an index-stable `BAR_ROW_INVALID`, fail the data gate, and cannot be hidden
  behind older valid rows. Malformed entries isolated under another pair do not
  affect the selected pair.
  Mapping rows also require a string timeframe exactly equal to `1D`, `4H`, or
  `1H`. Missing, null, non-string, empty, padded, case-variant, and unknown
  timeframe values emit an index-stable `BAR_ROW_INVALID`; they are never
  silently discarded. This classification is independently repeated by the
  auditor and remains isolated from rows belonging only to another pair.
  Rows with malformed or timezone-naive timestamps, non-finite or non-positive
  OHLC, or impossible high/low geometry cannot become evidence metadata. Any
  rejected row in a required timeframe emits `BAR_ROW_INVALID`, fails the
  binding data gate, and binds the decision to `NO_TRADE_DATA`; older valid
  indicators cannot preserve directional authority around unsafe raw evidence.
  Timestamp parsing, UTC conversion, and formatting also fail safely at
  datetime boundaries. Macro freshness is checked from a guarded UTC age
  calculation without subtracting the 24-hour policy from the capture time.
  Caller-controlled numeric conversion also rejects integers too large for a
  Python float across OHLC, proposed levels, confidence, reward-to-risk, and
  numerical claims without allowing an overflow exception to escape.
- During audit, the complete reported bar and indicator evidence objects must
  exactly match the independently derived JSON-safe structures, including
  timeframe and field sets, finite prices and indicators, counts, sources,
  timestamps, freshness, and history status. Top-level capture time and data
  provider must also match the valid bar evidence. Provider provenance is
  independently derived from every valid historical row used by the analysis:
  every row requires a non-empty string source and exactly one source must be
  present across 1D, 4H, and 1H. Mixed sources emit
  `MIXED_DATA_PROVIDERS`, set `data_provider` to null, fail the data gate, and
  bind the decision to `NO_TRADE_DATA`; missing or invalid sources retain the
  existing missing-attribution failure. The compatibility provider argument
  cannot override provenance derived from valid bars.
- The complete five-agent conclusions object must exactly match conclusions
  independently recomputed from the derived data, conflict, risk, and macro
  states. Missing, extra, malformed, altered, or contradictory content emits
  `AGENT_CONCLUSIONS_MISMATCH`.
- Binding data- and risk-gate reasons must be lists that exactly match the
  independently derived reasons, including their order and multiplicity.
- Slash symbols and supported `.FX` symbols normalize to an isolated canonical
  pair. Bare or malformed pairs fail safely.
- Non-JPY prices use five decimals and JPY-quoted prices use three decimals.
- Numerical claims are auditable against an explicit snapshot reference.
  Claims must be JSON lists of exact claim objects, use a supported price or
  indicator kind, use the report's canonical pair when a pair is explicit,
  and carry a timezone-aware timestamp that exactly matches the referenced
  bar.
  Price claims are limited to exact `<timeframe>.latest.open|high|low|close`
  references. Indicator claims are limited to exact
  `<timeframe>.indicators.<supported_indicator>` references in the closed
  indicator namespace exposed by each snapshot. A kind/reference mismatch or
  malformed, case-variant, unsupported-timeframe, or unknown path emits
  `CLAIM_KIND_REFERENCE_MISMATCH`, even when its value and provenance match.
  Unsupported prices or levels, missing provenance, zero-filled spot-FX volume
  interpretations, confidence-cap violations, unverified macro, and gate
  overrides produce machine-readable error codes.
- Confidence component values must be numeric, finite, non-negative, and no
  greater than their component maxima. The auditor independently recomputes
  the data, conflict, macro, and risk states and requires every component,
  component name, total, and cap to equal the resulting confidence contract.
  The overall cap is 50 without the complete verified-current-macro contract
  and 100 only when that contract passes. A caller-reported cap is never
  authoritative.

## Deterministic fixture evidence

The Batch 1 tests use generated OHLC fixtures labelled `SIMULATED`. They cover
EUR/USD, GBP/USD, and USD/JPY in slash and `.FX` forms, with independent source,
capture time, last-bar time, freshness, and history assertions for 1D, 4H, and
1H. They also prove pair isolation and formatting precision.

Safe-state scenarios cover missing timeframes, stale intraday data,
insufficient history, 1D/4H conflict, low or invalid reward-to-risk, weak or
flat momentum, volatility spike, abnormal gap, a normal forex weekend gap,
empty/provider-failure output, malformed pairs, and unavailable current macro.
These tests validate contracts and failure behavior; they are not live market
analysis. Deterministic fixtures are not live market evidence, and these tests
do not establish win rate, accuracy, profitability, or investor readiness.

Adversarial fixtures exercise invented prices, unsupported entry/stop/target
references, absent source metadata or timestamps, unsupported spot-FX
volume/order-flow language, excessive confidence, unverified macro, and an
attempted downstream risk-gate override. Additional regressions cover NaN and
both infinities in every proposed level, strict JSON serialization, missing or
invalid level references, value mismatches, arbitrary and cross-pair levels,
unsafe direction types, unsupported proposed-level fields, conflicting
provider arguments, and complete agent-conclusion mutations and contradictions,
missing or invalid binding-gate statuses, caller-controlled cap inflation,
component/total mismatches, excessive and negative components, non-finite
confidence, complete final-decision mismatches, independently audited
reward-to-risk values, consistent macro conclusions, deterministic macro
freshness boundaries, rejected-newest-bar metadata isolation, and valid
grounded long/short and 50/100-cap reports.
Huge positive and negative integer regressions cover every OHLC and proposed
level field, confidence components and totals, and numerical market claims.
Independent-gate regressions also cover stale and wrongly sourced grounding
behind a forged data-gate pass; invalid geometry, sub-2.0 reward-to-risk,
directional conflict, and decision/direction mismatch behind a forged risk-gate
pass; and a verified macro missing its claim.
Complete-evidence regressions alter prices, indicators, sources, capture and
last-bar timestamps, freshness, fields, and top-level evidence metadata.
Binding-explanation regressions remove, add, reorder, duplicate, and replace
both data- and risk-gate reason lists with non-list values.
Systematic closure regressions exercise JSON edge values at both public
functions; every required top-level field and exact top-level field sets;
every security-relevant top-level contract; forbidden `LIVE` labels;
deterministic fixture/source label consistency; deterministic disclosures; exact,
contradictory, stale, future, malformed, missing, and additional macro content;
non-finite claim identifiers in strict audit serialization; malformed
historical rows; timezone-naive bar timestamps; and missing, additional,
wrong-kind, wrong-pair, naive-timestamp, and non-finite numerical claims.

## Actual runtime evidence

The repository's existing runtime tests demonstrate that multi-timeframe
grounding is fetched and persisted, run provider/model metadata round-trips,
and failed upstream tasks block downstream tasks. Batch 1 also re-runs the
existing focused TradeCoreFX/forex suite and the new deterministic contracts.

No live TradeCoreFX market report was produced for this batch. No configured
LLM or live provider was assumed, no credentials were inspected, and no
network-derived result is represented as fixture evidence.

## Unavailable dependencies

- A configured model is required to execute the five LLM workers end to end.
- A reachable live market-data provider is required for a genuinely captured
  current run.
- Verified current macro support requires a retrieval path that preserves the
  authoritative source, observation time, and URL. The local-safe preset has no
  such tool, there is no verified production macro connector, and the workflow
  therefore reports `UNAVAILABLE`.

## Remaining risks

- The deterministic auditor validates structured claims. Free-form prose from
  arbitrary presets is not a reliable machine-readable interface and is not
  treated as audited merely because it resembles the schema.
- Public provider bars are not broker-executable quotes and may differ by
  vendor, timezone, session, spread, or revision policy.
- Spot FX has no centralized volume feed in this workflow. Zero-filled volume
  remains unavailable evidence.
- Deterministic scenarios prove safe contract behavior, not predictive value,
  profitability, user adoption, or production availability.

## Deferred to Phases 6–15

- User-interface presentation and investor evidence dashboards.
- A verified current-macro connector and source allowlist.
- Captured live-provider runs in a credentialed environment.
- Model-output schema enforcement at every external API boundary.
- Long-duration soak, concurrency, recovery, and provider-rate-limit testing.
- Broker-specific spread/slippage modelling and execution-quality research;
  live order placement remains outside the beta boundary.
- Calibration, walk-forward evaluation, portfolio aggregation, operational
  monitoring, and deployment readiness work.

## Validation log

Baseline before code changes:

```text
cd agent
pytest -q tests/test_tradecorefx_forex_desk.py tests/test_tradecorefx_local_safe_preset.py tests/test_swarm_grounding_forex.py tests/test_forex_features.py tests/test_forex_feature_grounding.py tests/test_swarm_grounding_runtime_multitimeframe.py tests/test_swarm_grounding_multitimeframe.py tests/test_swarm_output_contract.py tests/test_swarm_dag_gating.py tests/test_swarm_run_metadata.py tests/test_yfinance_forex.py
59 passed in 14.43s
```

Final Batch 1 suite:

```text
cd agent
pytest -q tests/test_tradecorefx_batch1_validation.py
326 passed in 51.15s
```

Final unique combined focused suite (the Batch 1 file plus every previously
used TradeCoreFX/forex test file, with each file listed exactly once):

```text
cd agent
pytest -q \
  tests/test_tradecorefx_batch1_validation.py \
  tests/test_tradecorefx_forex_desk.py \
  tests/test_tradecorefx_local_safe_preset.py \
  tests/test_swarm_grounding_forex.py \
  tests/test_forex_features.py \
  tests/test_forex_feature_grounding.py \
  tests/test_swarm_grounding_runtime_multitimeframe.py \
  tests/test_swarm_grounding_multitimeframe.py \
  tests/test_swarm_output_contract.py \
  tests/test_swarm_dag_gating.py \
  tests/test_swarm_run_metadata.py \
  tests/test_yfinance_forex.py
385 passed in 51.63s
```

Compilation also completed successfully:

```text
cd agent
python -m py_compile src/swarm/tradecorefx_validation.py tests/test_tradecorefx_batch1_validation.py
```
