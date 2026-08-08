from __future__ import annotations

import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ElevationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ElevationReason(str, Enum):
    OK = "ok"
    INVALID_ENVELOPE = "invalid_envelope"
    EXPIRED_ENVELOPE = "expired_envelope"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    SCRIPT_HASH_MISMATCH = "script_hash_mismatch"
    PARAMETER_DIGEST_MISMATCH = "parameter_digest_mismatch"
    WRONG_OPERATION = "wrong_operation"
    WRONG_EFFECT_CLASS = "wrong_effect_class"
    NOT_ADMIN_OPERATION = "not_admin_operation"
    GRANT_INVALID = "grant_invalid"
    GRANT_REUSED = "grant_reused"
    REPLAYED_NONCE = "replayed_nonce"
    NONCE_REUSED = "nonce_reused"
    TEMPLATE_MISMATCH = "template_mismatch"
    INVALID_PARAMETERS = "invalid_parameters"
    BROKER_IDENTITY_INVALID = "broker_identity_invalid"
    UAC_CANCELLED = "uac_cancelled"
    CANCELLED_BEFORE_ELEVATION = "cancelled_before_elevation"
    BROKER_TIMEOUT = "broker_timeout"
    BROKER_EXECUTION_TIMEOUT = "broker_execution_timeout"
    BROKER_FAILED = "broker_failed"
    WRONG_REQUEST_RESULT = "wrong_request_result"
    VERIFICATION_FAILED = "verification_failed"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("datetime_required")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise TypeError("mapping_required")
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ElevationRequest:
    """Main-side immutable handoff created only after confirmation and grant."""

    request_id: str
    proposal_id: str
    proposal_fingerprint: str
    grant_id: str
    operation_id: str
    template_id: str
    template_version: str
    script_sha256: str
    effect_class: str
    requires_admin: bool
    timeout_seconds: float
    created_at: datetime
    expires_at: datetime
    parameters: Mapping[str, Any]
    verification_plan_id: str
    nonce: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "proposal_id", "proposal_fingerprint", "grant_id",
                     "operation_id", "template_id", "template_version", "script_sha256",
                     "effect_class", "verification_plan_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError("invalid_request")
        if not isinstance(self.requires_admin, bool) or not self.requires_admin:
            raise ValueError("admin_required")
        if not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("invalid_timeout")
        created, expires = _utc(self.created_at), _utc(self.expires_at)
        if expires <= created:
            raise ValueError("invalid_expiry")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "parameters", _mapping(self.parameters))
        if self.nonce and (not isinstance(self.nonce, str) or len(self.nonce) > 128):
            raise ValueError("invalid_nonce")

    @classmethod
    def from_proposal(cls, proposal, grant, *, request_id: str | None = None,
                      nonce: str | None = None) -> "ElevationRequest":
        if grant is None or not getattr(proposal, "requires_admin", False):
            raise ValueError("admin_grant_required")
        parameters = dict(proposal.parameters)
        template = str(parameters.get("template_version", ""))
        return cls(
            request_id=request_id or secrets.token_urlsafe(18),
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.fingerprint,
            grant_id=grant.grant_id,
            operation_id=proposal.operation,
            template_id=template or f"windows_pet.{proposal.operation}",
            template_version="1",
            script_sha256=str(parameters.get("script_sha256", "")),
            effect_class=proposal.side_effect.value,
            requires_admin=proposal.requires_admin,
            timeout_seconds=proposal.timeout_seconds,
            created_at=grant.issued_at,
            expires_at=min(proposal.expires_at, grant.expires_at),
            parameters=parameters,
            verification_plan_id=proposal.verification_method,
            nonce=nonce or secrets.token_urlsafe(24),
        )


@dataclass(frozen=True)
class ElevationEnvelope:
    schema_version: int
    request_id: str
    proposal_id: str
    proposal_fingerprint: str
    grant_id: str
    operation_id: str
    template_id: str
    template_version: str
    script_sha256: str
    effect_class: str
    requires_admin: bool
    timeout_seconds: float
    created_at: datetime
    expires_at: datetime
    nonce: str
    parameter_digest: str
    verification_plan_id: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("invalid_schema_version")
        if not self.nonce or len(self.nonce) > 128:
            raise ValueError("invalid_nonce")
        request = ElevationRequest(
            self.request_id, self.proposal_id, self.proposal_fingerprint,
            self.grant_id, self.operation_id, self.template_id, self.template_version,
            self.script_sha256, self.effect_class, self.requires_admin,
            self.timeout_seconds, self.created_at, self.expires_at, self.parameters,
            self.verification_plan_id, self.nonce,
        )
        object.__setattr__(self, "created_at", request.created_at)
        object.__setattr__(self, "expires_at", request.expires_at)
        object.__setattr__(self, "parameters", request.parameters)


@dataclass(frozen=True)
class ElevationResult:
    request_id: str
    operation_id: str
    status: ElevationStatus
    result_code: str
    exit_code: int | None
    script_sha256: str
    started_at: datetime
    finished_at: datetime
    verification_hint: str = ""

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.request_id, self.operation_id, self.result_code, self.script_sha256)):
            raise ValueError("invalid_result")
        if not isinstance(self.status, ElevationStatus):
            object.__setattr__(self, "status", ElevationStatus(self.status))
        started, finished = _utc(self.started_at), _utc(self.finished_at)
        if finished < started:
            raise ValueError("invalid_result_time")
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)


@dataclass(frozen=True)
class ElevationLaunchOutcome:
    status: ElevationStatus
    reason: str
    result: ElevationResult | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class ElevationClientOutcome:
    status: ElevationStatus
    reason: str
    result: ElevationResult | None = None
    verification: str = ""
