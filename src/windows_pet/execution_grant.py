from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable

from .action_models import ActionProposal, ToolContract, proposal_fingerprint
from .policy_gate import PolicyGate


class GrantState(str, Enum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class GrantResultCode(str, Enum):
    CONSUMED = "consumed"
    NOT_FOUND = "not_found"
    ALREADY_USED = "already_used"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PROPOSAL_MISMATCH = "proposal_mismatch"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    POLICY_DENIED = "policy_denied"


@dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    proposal_id: str
    proposal_fingerprint: str
    issued_at: datetime
    expires_at: datetime


@dataclass
class _GrantRecord:
    grant: ExecutionGrant
    state: GrantState = GrantState.ACTIVE


class _ApprovedCapability:
    def __init__(self, session_id: str, proposal: ActionProposal):
        self.session_id = session_id
        self.proposal_id = proposal.proposal_id
        self.fingerprint = proposal.fingerprint


def _new_approved_capability(session_id: str, proposal: ActionProposal) -> object:
    return _ApprovedCapability(session_id, proposal)


@dataclass(frozen=True)
class GrantConsumeResult:
    success: bool
    reason: GrantResultCode


class ExecutionGrantStore:
    """Non-persistent, atomic grant state store; issuance is capability-gated."""

    def __init__(self, now: Callable[[], datetime] | None = None,
                 lifetime: timedelta = timedelta(seconds=90), id_factory=None, policy=None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lifetime = lifetime
        self.id_factory = id_factory or (lambda: secrets.token_urlsafe(24))
        self.policy = policy or PolicyGate()
        self._lock = threading.Lock()
        self._records: dict[str, _GrantRecord] = {}

    def _issue_capability(self, proposal: ActionProposal, capability: _ApprovedCapability) -> ExecutionGrant:
        if capability.proposal_id != proposal.proposal_id or capability.fingerprint != proposal.fingerprint:
            raise PermissionError("grant_capability_mismatch")
        issued = self.now()
        grant = ExecutionGrant(self.id_factory(), proposal.proposal_id, proposal.fingerprint, issued, issued + self.lifetime)
        with self._lock:
            self._records[grant.grant_id] = _GrantRecord(grant)
        return grant

    def issue(self, proposal: ActionProposal):
        raise PermissionError("grant_issue_requires_confirmation")

    def consume_for(self, grant_id: str, contract: ToolContract, proposal: ActionProposal) -> GrantConsumeResult:
        with self._lock:
            record = self._records.get(grant_id)
            if record is None: return GrantConsumeResult(False, GrantResultCode.NOT_FOUND)
            if record.state is GrantState.CANCELLED: return GrantConsumeResult(False, GrantResultCode.CANCELLED)
            if record.state is GrantState.CONSUMED: return GrantConsumeResult(False, GrantResultCode.ALREADY_USED)
            if self.now() >= record.grant.expires_at:
                record.state = GrantState.EXPIRED
                return GrantConsumeResult(False, GrantResultCode.EXPIRED)
            if record.grant.proposal_id != proposal.proposal_id: return GrantConsumeResult(False, GrantResultCode.PROPOSAL_MISMATCH)
            if record.grant.proposal_fingerprint != proposal.fingerprint or proposal.fingerprint != proposal_fingerprint(proposal): return GrantConsumeResult(False, GrantResultCode.FINGERPRINT_MISMATCH)
            if self.policy.evaluate(contract, proposal).decision.value != "require_confirmation": return GrantConsumeResult(False, GrantResultCode.POLICY_DENIED)
            record.state = GrantState.CONSUMED
            return GrantConsumeResult(True, GrantResultCode.CONSUMED)

    def cancel(self, grant_id: str) -> GrantResultCode:
        with self._lock:
            record = self._records.get(grant_id)
            if record is None: return GrantResultCode.NOT_FOUND
            if record.state is GrantState.ACTIVE:
                record.state = GrantState.CANCELLED
                return GrantResultCode.CANCELLED
            return GrantResultCode.ALREADY_USED if record.state is GrantState.CONSUMED else GrantResultCode.EXPIRED

    def clear(self) -> None:
        with self._lock:
            for record in self._records.values():
                if record.state is GrantState.ACTIVE:
                    record.state = GrantState.CANCELLED
            self._records.clear()


class ExecutionGrantIssuer:
    """Only the confirmation gate owns the private approval capability."""
    def __init__(self, store: ExecutionGrantStore):
        self._store = store

    def _issue(self, capability: object, proposal: ActionProposal) -> ExecutionGrant:
        if not isinstance(capability, _ApprovedCapability):
            raise PermissionError("grant_capability_required")
        return self._store._issue_capability(proposal, capability)
