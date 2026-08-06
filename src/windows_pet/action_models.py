from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping


class SideEffect(str, Enum):
    READ_ONLY = "read_only"
    APPLICATION_LAUNCH = "application_launch"
    FILE_CREATE = "file_create"
    FILE_MODIFY = "file_modify"
    FILE_MOVE = "file_move"
    FILE_DELETE = "file_delete"
    PROCESS_CONTROL = "process_control"
    SYSTEM_CHANGE = "system_change"
    INSTALLATION = "installation"
    EXTERNAL_SEND = "external_send"


class ConfirmationType(str, Enum):
    NONE = "none"
    SIMPLE = "simple"
    BEFORE_AFTER = "before_after"
    PLAN_IMPACT = "plan_impact"
    EXTERNAL_SEND = "external_send"
    INSTALLATION = "installation"


class PolicyDecision(str, Enum):
    ALLOW_READ_ONLY = "allow_read_only"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


class ConfirmationDecision(str, Enum):
    APPROVE = "approve"
    CANCEL = "cancel"
    REVISE = "revise"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ToolContract:
    name: str
    version: str
    operation: str
    side_effect: SideEffect
    confirmation: ConfirmationType
    reversible: bool
    requires_admin: bool
    cancellation_support: bool
    timeout_seconds: float
    verification_method: str
    audit_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionTarget:
    kind: str
    identifier: str
    display_name: str


@dataclass(frozen=True)
class ActionPreview:
    operation: str
    impact: str
    button_label: str
    before: str = ""
    after: str = ""
    category: ConfirmationType = ConfirmationType.SIMPLE


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    task_id: str
    tool_name: str
    tool_version: str
    operation: str
    side_effect: SideEffect
    confirmation_type: ConfirmationType
    target: ActionTarget
    parameters: Any
    preview: ActionPreview
    reversible: bool
    requires_admin: bool
    verification_method: str
    created_at: datetime
    expires_at: datetime
    fingerprint: str


def _freeze(value: Any, field: str = "parameters") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("invalid_parameter_type")
        return value
    if isinstance(value, list):
        return tuple(_freeze(item, field) for item in value)
    if isinstance(value, dict):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid_parameter_type")
            if re.search(r"api[-_]?key|password|token|authorization|cookie|secret|private[-_]?key|credential", key, re.I):
                raise ValueError("sensitive_parameter")
            frozen[key] = _freeze(item, key)
        return MappingProxyType(frozen)
    raise ValueError("invalid_parameter_type")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def proposal_fingerprint(proposal: ActionProposal) -> str:
    payload = {"task_id": proposal.task_id, "tool": proposal.tool_name, "version": proposal.tool_version,
               "operation": proposal.operation, "side_effect": proposal.side_effect.value,
               "confirmation": proposal.confirmation_type.value, "target": proposal.target.__dict__,
               "parameters": _jsonable(proposal.parameters), "preview": {"operation": proposal.preview.operation,
               "impact": proposal.preview.impact, "button_label": proposal.preview.button_label,
               "before": proposal.preview.before, "after": proposal.preview.after,
               "category": proposal.preview.category.value}, "reversible": proposal.reversible,
               "requires_admin": proposal.requires_admin, "verification": proposal.verification_method,
               "created": proposal.created_at.isoformat(), "expires": proposal.expires_at.isoformat()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class ActionProposalFactory:
    """Creates immutable proposals and owns IDs, timestamps, and fingerprints."""

    def __init__(self, id_factory: Callable[[], str] | None = None,
                 now: Callable[[], datetime] | None = None, lifetime: timedelta = timedelta(minutes=5)):
        self.id_factory = id_factory or (lambda: secrets.token_urlsafe(24))
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lifetime = lifetime

    def create(self, contract: ToolContract, task_id: str, target: ActionTarget,
               parameters: Mapping[str, Any], preview: ActionPreview) -> ActionProposal:
        frozen = _freeze(dict(parameters))
        created = self.now()
        expires = created + self.lifetime
        payload = {"task_id": task_id, "tool": contract.name, "version": contract.version,
                   "operation": contract.operation, "side_effect": contract.side_effect.value,
                   "confirmation": contract.confirmation.value, "target": target.__dict__,
                   "parameters": _jsonable(frozen), "preview": {"operation": preview.operation, "impact": preview.impact,
                   "button_label": preview.button_label, "before": preview.before, "after": preview.after,
                   "category": preview.category.value},
                   "reversible": contract.reversible, "requires_admin": contract.requires_admin,
                   "verification": contract.verification_method, "created": created.isoformat(),
                   "expires": expires.isoformat()}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        fingerprint = hashlib.sha256(encoded).hexdigest()
        return ActionProposal(self.id_factory(), task_id, contract.name, contract.version, contract.operation,
                              contract.side_effect, contract.confirmation, target, frozen, preview,
                              contract.reversible, contract.requires_admin, contract.verification_method,
                              created, expires, fingerprint)
