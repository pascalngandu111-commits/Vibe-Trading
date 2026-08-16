"""Internal, default-deny macro-evidence trust boundary for TradeCoreFX.

Connectors may submit observations to an operator-created registry. Public API
payloads and model prose never reach this boundary and cannot grant trust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping
from urllib.parse import urlparse

from src.swarm.tradecorefx_validation import BETA_CURRENCIES, normalize_forex_pair


@dataclass(frozen=True)
class MacroEvidence:
    claim: str
    source_identity: str
    observed_at: str
    retrieved_at: str | None
    source_url: str
    applicability: str
    provenance: str
    trust_state: str = "UNTRUSTED"
    redirected: bool = False


@dataclass(frozen=True)
class _AuthorizedMacroEvidence:
    """Registry-owned capability; never constructed from JSON/model content."""

    evidence: MacroEvidence
    registry: object


class TrustedMacroRegistry:
    """Validate connector evidence against an explicit HTTPS host allowlist."""

    def __init__(
        self,
        allowed_hosts: Iterable[str] = (),
        *,
        now: Callable[[], datetime] | None = None,
        maximum_age: timedelta = timedelta(hours=24),
    ) -> None:
        self._allowed_hosts = frozenset(
            host.strip().lower().rstrip(".") for host in allowed_hosts if host.strip()
        )
        if maximum_age <= timedelta(0):
            raise ValueError("maximum_age must be positive")
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._maximum_age = maximum_age

    def authorize(
        self, evidence: MacroEvidence | Mapping[str, object]
    ) -> _AuthorizedMacroEvidence | None:
        # A trusted clock is sampled once, before inspecting attacker-controlled
        # timestamps, so every comparison in this attempt uses one instant.
        try:
            now = self._now()
        except Exception:
            return None
        try:
            item = evidence if isinstance(evidence, MacroEvidence) else MacroEvidence(**evidence)
        except (TypeError, ValueError):
            return None
        parsed = urlparse(item.source_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not self._allowed_hosts or parsed.scheme != "https" or host not in self._allowed_hosts:
            return None
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path
            or item.trust_state != "UNTRUSTED"
            or item.redirected
        ):
            return None
        if not all(isinstance(v, str) and v.strip() for v in (
            item.claim, item.source_identity, item.observed_at, item.source_url,
            item.applicability, item.provenance,
        )):
            return None
        if item.provenance not in {"INTERNAL_CONNECTOR", "TRUSTED_REGISTRY"}:
            return None
        if not _aware_timestamp(item.observed_at) or (
            item.retrieved_at is not None and not _aware_timestamp(item.retrieved_at)
        ):
            return None
        observed = _parse_timestamp(item.observed_at)
        retrieved = _parse_timestamp(item.retrieved_at) if item.retrieved_at is not None else now
        now_utc = _utc_datetime(now)
        if (
            observed is None
            or retrieved is None
            or now_utc is None
            or observed > now_utc
            or retrieved > now_utc
            or now_utc - observed > self._maximum_age
            or not timedelta(0) <= retrieved - observed <= self._maximum_age
        ):
            return None
        applicability = item.applicability.strip().upper()
        if normalize_forex_pair(applicability) is None and applicability not in BETA_CURRENCIES:
            return None
        trusted = MacroEvidence(
            claim=item.claim.strip(), source_identity=item.source_identity.strip(),
            observed_at=item.observed_at, retrieved_at=item.retrieved_at,
            source_url=item.source_url, applicability=applicability,
            provenance="TRUSTED_REGISTRY", trust_state="TRUSTED_INTERNAL", redirected=False,
        )
        return _AuthorizedMacroEvidence(trusted, self)

    def for_pair(self, authorized: object, pair: str) -> dict[str, str] | None:
        canonical = normalize_forex_pair(pair)
        if (
            canonical is None
            or not isinstance(authorized, _AuthorizedMacroEvidence)
            or authorized.registry is not self
        ):
            return None
        evidence = authorized.evidence
        if evidence.applicability not in {canonical, *canonical.split("/")}:
            return None
        return {
            "claim": evidence.claim,
            "source": evidence.source_identity,
            "observed_at": evidence.observed_at,
            "url": evidence.source_url,
        }


def _aware_timestamp(value: str) -> bool:
    return _parse_timestamp(value) is not None


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
