# TradeCoreFX Phase 6–8 acceptance boundary

This document describes engineering harnesses, not deployment results. Recorded fixtures are explicitly non-live and cannot satisfy provider, deployment, accessibility, legal, or performance acceptance.

## Acceptance lanes

| Lane | Automated here | Remaining gate |
|---|---|---|
| Deterministic CI | Pair normalization, fixed server-owned run creation, trust forgery, provider failure states, bounded concurrency, isolation and redaction. | CI must pass on the review commit. |
| Credentialed provider acceptance | Not implemented in this repository: there is no configured credentialed FX provider adapter, and no executable mode is advertised. | Implement or supply an approved real adapter externally, invoke it only with explicit opt-in, and record sanitized results. |
| Deployment-browser acceptance | No claimed result. | Test authentication, reverse proxy, HTTPS, CORS, rate limits, responsive layouts and supported browsers against the target deployment. |
| Accessibility review | Automated labels, keyboard-native controls, focusable form elements, live status/error announcements, table semantics and absence of execution controls. | Manual screen-reader, zoom/reflow, contrast and focus-order review. |
| Soak/concurrency/recovery | Bounded deterministic concurrency and safe provider-state fixtures; persistent store reconciliation remains covered by store tests. | Opt-in duration/concurrency soak, kill/restart drill, backup/restore and monitoring-alert drill in an isolated target-like environment. |
| Legal/licensing review | No automated acceptance claim. | Counsel/owners review privacy, retention, market-data licenses, disclosures and subscription terms. |
| Demonstrated performance research | No automated acceptance claim. | Phase 10 leakage-controlled historical calibration and walk-forward evidence plus independent review. |

## Commands

Deterministic contract tests:

```bash
cd agent
pytest -q tests/test_tradecorefx_phase6_pair_runs.py
pytest -q tests/test_tradecorefx_phase7_macro_contract.py
pytest -q tests/test_tradecorefx_phase8_harness.py
```

The bounded soak is disabled unless `--soak` is explicit. It requires duration `1..86400` seconds and concurrency `1..32`, and emits strict machine-readable JSON:

```bash
cd agent
python scripts/tradecorefx_acceptance.py --soak --duration-seconds 60 --concurrency 4 --report /tmp/tradecorefx-soak.json
```

Credentialed provider acceptance remains external and unimplemented. The repository's
TradeCoreFX adapter is yfinance, which is a real public-data adapter but is not a
credentialed provider. Consequently the script deliberately has no
`--credentialed-provider` option; passing it is a command-line error and cannot fall
back to fixture evidence. Any future credentialed lane must use an approved real FX
adapter, remain explicitly opt-in, bound its calls, redact failures and provider
payloads, and label its evidence separately from fixtures.

## Required manual record

Record environment/version, reviewer, timestamps, bounded configuration, failures, redacted artifacts and acceptance decision. Screen-reader behavior, reverse-proxy headers, HTTPS, CORS, authentication/rate limiting, backup/restore, recovery objectives, monitoring alerts, privacy, legal and licensing remain unpassed until that record is reviewed. Multi-hour soak is not part of this engineering closure. No fixture is current macro or provider evidence, and no Phase 7 credentialed or Phase 8 deployment acceptance is claimed.
