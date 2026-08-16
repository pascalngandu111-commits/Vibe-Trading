from datetime import datetime, timedelta, timezone

from src.swarm.tradecorefx_macro import MacroEvidence, TrustedMacroRegistry


NOW = datetime(2026, 8, 15, 10, 5, tzinfo=timezone.utc)


def registry(hosts=("macro.example.test",)):
    return TrustedMacroRegistry(hosts, now=lambda: NOW)


def evidence(**changes):
    values = dict(
        claim="Policy rate unchanged", source_identity="Example central bank",
        observed_at="2026-08-15T10:00:00Z", retrieved_at="2026-08-15T10:05:00Z",
        source_url="https://macro.example.test/releases/1", applicability="EUR/USD",
        provenance="INTERNAL_CONNECTOR",
    )
    values.update(changes)
    return MacroEvidence(**values)


def test_internal_registry_authorizes_and_isolates_pair():
    trusted_registry = registry()
    trusted = trusted_registry.authorize(evidence())
    assert trusted is not None
    assert trusted_registry.for_pair(trusted, "EUR/USD")["claim"] == "Policy rate unchanged"
    assert trusted_registry.for_pair(trusted, "GBP/USD") is None


def test_empty_allowlist_and_forged_trust_default_deny():
    assert registry(()).authorize(evidence()) is None
    assert registry().authorize(evidence(trust_state="TRUSTED_INTERNAL")) is None


def test_http_redirect_like_unapproved_and_malformed_evidence_fail_closed():
    trusted_registry = registry()
    assert trusted_registry.authorize(evidence(source_url="http://macro.example.test/releases/1")) is None
    assert trusted_registry.authorize(evidence(source_url="https://other.example.test/releases/1")) is None
    assert trusted_registry.authorize(evidence(redirected=True)) is None
    assert trusted_registry.authorize(evidence(source_url="https://macro.example.test")) is None
    assert trusted_registry.authorize(evidence(source_url="https://macro.example.test/releases/1?token=secret")) is None
    assert trusted_registry.authorize(evidence(observed_at="not-a-date")) is None
    assert trusted_registry.authorize(evidence(applicability="BTC/USD")) is None


def test_stale_and_future_observations_fail_closed():
    trusted_registry = registry()
    assert trusted_registry.authorize(evidence(observed_at="2026-08-13T10:00:00Z")) is None
    assert trusted_registry.authorize(evidence(observed_at="2026-08-15T10:10:00Z")) is None
    assert trusted_registry.authorize(evidence(retrieved_at="2026-08-15T10:06:00Z")) is None
    assert trusted_registry.authorize(evidence(retrieved_at=None, observed_at="2026-08-14T10:04:59Z")) is None


def test_registry_age_boundary_and_configuration_are_deterministic():
    trusted = registry().authorize(evidence(retrieved_at=None, observed_at="2026-08-14T10:05:00Z"))
    assert trusted is not None
    try:
        TrustedMacroRegistry(maximum_age=timedelta(0))
    except ValueError as exc:
        assert str(exc) == "maximum_age must be positive"
    else:
        raise AssertionError("non-positive freshness window was accepted")


def test_old_observation_and_old_retrieval_are_rejected_as_replay():
    assert registry().authorize(evidence(
        observed_at="2026-08-14T09:00:00Z", retrieved_at="2026-08-14T09:01:00Z"
    )) is None


def test_observation_age_boundary_is_inclusive_to_the_microsecond():
    assert registry().authorize(evidence(
        observed_at="2026-08-14T10:05:00Z", retrieved_at="2026-08-14T10:06:00Z"
    )) is not None
    assert registry().authorize(evidence(
        observed_at="2026-08-14T10:04:59.999999Z", retrieved_at="2026-08-14T10:06:00Z"
    )) is None


def test_retrieval_order_and_future_retrieval_fail_closed():
    assert registry().authorize(evidence(retrieved_at="2026-08-15T09:59:59Z")) is None
    assert registry().authorize(evidence(retrieved_at="2026-08-15T10:05:00.000001Z")) is None


def test_clock_is_called_once_per_attempt_and_naive_times_fail_closed():
    calls = []
    trusted_registry = TrustedMacroRegistry(
        ("macro.example.test",), now=lambda: calls.append(NOW) or NOW
    )
    assert trusted_registry.authorize(evidence(retrieved_at=None)) is not None
    assert calls == [NOW]
    assert TrustedMacroRegistry(("macro.example.test",), now=lambda: NOW.replace(tzinfo=None)).authorize(evidence()) is None
    assert registry().authorize(evidence(observed_at="2026-08-15T10:00:00")) is None
    assert registry().authorize(evidence(retrieved_at="2026-08-15T10:05:00")) is None
