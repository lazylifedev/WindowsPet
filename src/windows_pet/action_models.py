from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from dataclasses import dataclass, fields, is_dataclass
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
    name: str = "local_inspection"
    version: str = "1"
    operation: str = "inspect_local_pc"
    side_effect: SideEffect = SideEffect.READ_ONLY
    confirmation: ConfirmationType = ConfirmationType.NONE
    reversible: bool = True
    requires_admin: bool = False
    cancellation_support: bool = True
    timeout_seconds: float = 5.0
    verification_method: str = "structured local inspection result"
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
class SimpleActionPreview(ActionPreview):
    category: ConfirmationType = ConfirmationType.SIMPLE


@dataclass(frozen=True)
class BeforeAfterActionPreview(ActionPreview):
    change_summary: str = ""
    backup_available: bool = False
    category: ConfirmationType = ConfirmationType.BEFORE_AFTER


@dataclass(frozen=True)
class PlanImpactActionPreview(ActionPreview):
    purpose: str = ""
    steps: tuple[str, ...] = ()
    restart_possible: bool = False
    rollback_summary: str = ""
    category: ConfirmationType = ConfirmationType.PLAN_IMPACT


@dataclass(frozen=True)
class ExternalSendActionPreview(ActionPreview):
    recipient: str = ""
    attachment_display_names: tuple[str, ...] = ()
    destination_type: str = ""
    visibility: str = ""
    category: ConfirmationType = ConfirmationType.EXTERNAL_SEND


@dataclass(frozen=True)
class InstallationActionPreview(ActionPreview):
    product_name: str = ""
    publisher: str = ""
    package_id: str = ""
    installation_method: str = ""
    restart_possible: bool = False
    expected_changes: str = ""
    category: ConfirmationType = ConfirmationType.INSTALLATION


def validate_contract(contract: ToolContract) -> None:
    if not all(isinstance(value, str) and value.strip() for value in (contract.name, contract.version, contract.operation, contract.verification_method)):
        raise ValueError("invalid_contract")
    if not isinstance(contract.side_effect, SideEffect) or not isinstance(contract.confirmation, ConfirmationType):
        raise ValueError("invalid_contract")
    if contract.side_effect is SideEffect.READ_ONLY and contract.confirmation is not ConfirmationType.NONE:
        raise ValueError("invalid_confirmation_type")
    if contract.side_effect is not SideEffect.READ_ONLY and contract.confirmation is ConfirmationType.NONE:
        raise ValueError("confirmation_required")
    if contract.timeout_seconds <= 0 or not math.isfinite(contract.timeout_seconds):
        raise ValueError("invalid_timeout")
    if not all(isinstance(field, str) for field in contract.audit_fields):
        raise ValueError("invalid_audit_fields")


def validate_preview(preview: ActionPreview, confirmation: ConfirmationType) -> None:
    required = {
        ConfirmationType.SIMPLE: (SimpleActionPreview, ("operation", "impact", "button_label")),
        ConfirmationType.BEFORE_AFTER: (BeforeAfterActionPreview, ("operation", "before", "after", "change_summary", "impact", "button_label")),
        ConfirmationType.PLAN_IMPACT: (PlanImpactActionPreview, ("purpose", "impact", "rollback_summary", "button_label")),
        ConfirmationType.EXTERNAL_SEND: (ExternalSendActionPreview, ("recipient", "destination_type", "visibility", "button_label")),
        ConfirmationType.INSTALLATION: (InstallationActionPreview, ("product_name", "publisher", "package_id", "installation_method", "expected_changes", "button_label")),
    }
    if confirmation is ConfirmationType.NONE:
        return
    expected, names = required.get(confirmation, (None, ()))
    if expected is None or type(preview) is not expected:
        raise ValueError("preview_mismatch")
    for name in names:
        if not isinstance(getattr(preview, name), str) or not getattr(preview, name).strip():
            raise ValueError("invalid_preview")
    if isinstance(preview, PlanImpactActionPreview) and not preview.steps:
        raise ValueError("invalid_preview")
    if isinstance(preview, ExternalSendActionPreview) and any(not item.strip() for item in preview.attachment_display_names):
        raise ValueError("invalid_preview")


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
    cancellation_support: bool
    timeout_seconds: float
    audit_fields: tuple[str, ...]
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
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def proposal_fingerprint(proposal: ActionProposal) -> str:
    payload = {field.name: _jsonable(getattr(proposal, field.name)) for field in fields(proposal) if field.name not in ("fingerprint", "proposal_id")}
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
        validate_contract(contract)
        if not task_id.strip() or not all(isinstance(value, str) and value.strip() for value in (target.kind, target.identifier, target.display_name)):
            raise ValueError("invalid_target")
        if not isinstance(preview, ActionPreview):
            raise ValueError("preview_mismatch")
        validate_preview(preview, contract.confirmation)
        frozen = _freeze(dict(parameters))
        created = self.now()
        expires = created + self.lifetime
        proposal_id = self.id_factory()
        draft = ActionProposal(proposal_id, task_id, contract.name, contract.version, contract.operation,
                               contract.side_effect, contract.confirmation, target, frozen, preview,
                               contract.reversible, contract.requires_admin, contract.cancellation_support,
                               contract.timeout_seconds, contract.audit_fields, contract.verification_method,
                              created, expires, "")
        return ActionProposal(*[getattr(draft, field.name) if field.name != "fingerprint" else proposal_fingerprint(draft)
                                for field in fields(draft)])
