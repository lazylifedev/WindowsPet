from __future__ import annotations
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
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
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_NOT_APPROVED = "session_not_approved"
    SESSION_MISMATCH = "session_mismatch"
    PROPOSAL_MISMATCH = "proposal_mismatch"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    POLICY_DENIED = "policy_denied"


@dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    confirmation_session_id: str
    proposal_id: str
    proposal_fingerprint: str
    issued_at: datetime
    expires_at: datetime


@dataclass
class _Record:
    grant: ExecutionGrant
    state: GrantState = GrantState.ACTIVE


@dataclass(frozen=True)
class GrantConsumeResult:
    success: bool
    reason: GrantResultCode


class ExecutionGrantStore:
    """Non-persistent grant state; it has no public issuance API."""
    def __init__(self, now=None, lifetime=timedelta(seconds=90), id_factory=None, policy=None, session_lookup=None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lifetime = lifetime
        self.id_factory = id_factory or (lambda: secrets.token_urlsafe(24))
        self.policy = policy or PolicyGate()
        self.session_lookup = session_lookup or (lambda _: None)
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}

    def _record_grant(self, proposal: ActionProposal, session_id: str) -> ExecutionGrant:
        if not session_id:
            raise PermissionError("session_required")
        issued = self.now()
        grant = ExecutionGrant(self.id_factory(), session_id, proposal.proposal_id, proposal.fingerprint, issued, issued + self.lifetime)
        with self._lock:
            self._records[grant.grant_id] = _Record(grant)
        return grant

    def consume_for(self, grant_id: str, contract: ToolContract, proposal: ActionProposal) -> GrantConsumeResult:
        with self._lock:
            record = self._records.get(grant_id)
            if record is None: return GrantConsumeResult(False, GrantResultCode.NOT_FOUND)
            if record.state is GrantState.CANCELLED: return GrantConsumeResult(False, GrantResultCode.CANCELLED)
            if record.state is GrantState.CONSUMED: return GrantConsumeResult(False, GrantResultCode.ALREADY_USED)
            if self.now() >= record.grant.expires_at:
                record.state = GrantState.EXPIRED
                return GrantConsumeResult(False, GrantResultCode.EXPIRED)
            session = self.session_lookup(record.grant.confirmation_session_id)
            if session is None: return GrantConsumeResult(False, GrantResultCode.SESSION_NOT_FOUND)
            if getattr(session, "state", None) != "approved": return GrantConsumeResult(False, GrantResultCode.SESSION_NOT_APPROVED)
            if session.proposal_id != record.grant.proposal_id or session.proposal_fingerprint != record.grant.proposal_fingerprint: return GrantConsumeResult(False, GrantResultCode.SESSION_MISMATCH)
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
                if record.state is GrantState.ACTIVE: record.state = GrantState.CANCELLED
            self._records.clear()
