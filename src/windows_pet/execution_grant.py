from __future__ import annotations
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from .action_models import ActionProposal, ToolContract, proposal_fingerprint
from .policy_gate import PolicyGate
from .audit_log import AuditEvent, NullAuditSink


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
    def __init__(self, now=None, lifetime=timedelta(seconds=90), id_factory=None, policy=None, session_lookup=None, audit=None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lifetime = lifetime
        self.id_factory = id_factory or (lambda: secrets.token_urlsafe(24))
        self.policy = policy or PolicyGate()
        self.session_lookup = session_lookup or (lambda _: None)
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}
        self.audit = audit or NullAuditSink()

    def _record_grant(self, proposal: ActionProposal, session_id: str) -> ExecutionGrant:
        if not session_id:
            raise PermissionError("session_required")
        issued = self.now()
        grant = ExecutionGrant(self.id_factory(), session_id, proposal.proposal_id, proposal.fingerprint, issued, issued + self.lifetime)
        with self._lock:
            self._records[grant.grant_id] = _Record(grant)
        return grant

    def _audit_rejection(self, code, grant_id="", proposal=None, contract=None):
        self.audit.write(AuditEvent("grant_expired" if code is GrantResultCode.EXPIRED else "grant_rejected", result_code=code.value, grant_id=grant_id, proposal_id=getattr(proposal, "proposal_id", ""), proposal_fingerprint=getattr(proposal, "fingerprint", ""), tool_name=getattr(contract, "name", ""), tool_version=getattr(contract, "version", ""), operation=getattr(contract, "operation", ""), side_effect=getattr(getattr(contract, "side_effect", None), "value", "")))

    def consume_for(self, grant_id: str, contract: ToolContract, proposal: ActionProposal) -> GrantConsumeResult:
        with self._lock:
            record = self._records.get(grant_id)
            if record is None: result = GrantConsumeResult(False, GrantResultCode.NOT_FOUND)
            elif record.state is GrantState.CANCELLED: result = GrantConsumeResult(False, GrantResultCode.CANCELLED)
            elif record.state is GrantState.CONSUMED: result = GrantConsumeResult(False, GrantResultCode.ALREADY_USED)
            elif self.now() >= record.grant.expires_at: record.state = GrantState.EXPIRED; result = GrantConsumeResult(False, GrantResultCode.EXPIRED)
            else:
                session = self.session_lookup(record.grant.confirmation_session_id)
                if session is None: result = GrantConsumeResult(False, GrantResultCode.SESSION_NOT_FOUND)
                elif getattr(getattr(session, "state", None), "value", getattr(session, "state", None)) != "approved": result = GrantConsumeResult(False, GrantResultCode.SESSION_NOT_APPROVED)
                elif session.proposal_id != record.grant.proposal_id or session.proposal_fingerprint != record.grant.proposal_fingerprint: result = GrantConsumeResult(False, GrantResultCode.SESSION_MISMATCH)
                elif record.grant.proposal_id != proposal.proposal_id: result = GrantConsumeResult(False, GrantResultCode.PROPOSAL_MISMATCH)
                elif record.grant.proposal_fingerprint != proposal.fingerprint or proposal.fingerprint != proposal_fingerprint(proposal): result = GrantConsumeResult(False, GrantResultCode.FINGERPRINT_MISMATCH)
                elif self.policy.evaluate(contract, proposal).decision.value != "require_confirmation": result = GrantConsumeResult(False, GrantResultCode.POLICY_DENIED)
                else:
                    record.state = GrantState.CONSUMED
                    self.audit.write(AuditEvent("grant_consumed", grant_id=grant_id, proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint, tool_name=contract.name, tool_version=contract.version, operation=contract.operation, side_effect=contract.side_effect.value))
                    return GrantConsumeResult(True, GrantResultCode.CONSUMED)
            self._audit_rejection(result.reason, grant_id, proposal, contract)
            return result

    def cancel(self, grant_id: str) -> GrantResultCode:
        with self._lock:
            record = self._records.get(grant_id)
            if record is None: return GrantResultCode.NOT_FOUND
            if record.state is GrantState.ACTIVE:
                record.state = GrantState.CANCELLED
                self.audit.write(AuditEvent("grant_cancelled", result_code=GrantResultCode.CANCELLED.value, grant_id=grant_id))
                return GrantResultCode.CANCELLED
            return GrantResultCode.ALREADY_USED if record.state is GrantState.CONSUMED else GrantResultCode.EXPIRED

    def clear(self) -> None:
        with self._lock:
            for record in self._records.values():
                if record.state is GrantState.ACTIVE: record.state = GrantState.CANCELLED
            self._records.clear()
