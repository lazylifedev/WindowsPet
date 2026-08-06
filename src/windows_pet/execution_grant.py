from __future__ import annotations
import threading
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .action_models import ActionProposal, ToolContract, proposal_fingerprint
from .policy_gate import PolicyGate
from enum import Enum


@dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    proposal_id: str
    proposal_fingerprint: str
    issued_at: datetime
    expires_at: datetime


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
class GrantConsumeResult:
    success: bool
    reason: GrantResultCode


class ExecutionGrantStore:
    """In-memory, atomic, one-time grant store."""
    def __init__(self, now=None, lifetime: timedelta = timedelta(seconds=90)):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lifetime = lifetime
        self._lock = threading.Lock()
        self._grants: dict[str, ExecutionGrant] = {}
        self._used: set[str] = set()
        self._cancelled: set[str] = set()

    def _issue(self, proposal: ActionProposal) -> ExecutionGrant:
        with self._lock:
            issued = self.now()
            grant = ExecutionGrant(secrets.token_urlsafe(24), proposal.proposal_id, proposal.fingerprint, issued, issued + self.lifetime)
            self._grants[grant.grant_id] = grant
            return grant

    def issue(self, proposal: ActionProposal) -> ExecutionGrant:
        raise PermissionError("grant_issue_requires_confirmation")

    def consume(self, grant_id: str, proposal: ActionProposal) -> bool:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None or grant_id in self._used or self.now() >= grant.expires_at: return False
            if grant.proposal_id != proposal.proposal_id or grant.proposal_fingerprint != proposal.fingerprint: return False
            self._used.add(grant_id)
            return True

    def consume_for(self, grant_id: str, contract: ToolContract, proposal: ActionProposal) -> GrantConsumeResult:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None: return GrantConsumeResult(False, GrantResultCode.NOT_FOUND)
            if grant_id in self._cancelled: return GrantConsumeResult(False, GrantResultCode.CANCELLED)
            if grant_id in self._used: return GrantConsumeResult(False, GrantResultCode.ALREADY_USED)
            if self.now() >= grant.expires_at: return GrantConsumeResult(False, GrantResultCode.EXPIRED)
            if grant.proposal_id != proposal.proposal_id: return GrantConsumeResult(False, GrantResultCode.PROPOSAL_MISMATCH)
            if grant.proposal_fingerprint != proposal.fingerprint or proposal.fingerprint != proposal_fingerprint(proposal): return GrantConsumeResult(False, GrantResultCode.FINGERPRINT_MISMATCH)
            if PolicyGate().evaluate(contract, proposal).decision.value != "require_confirmation": return GrantConsumeResult(False, GrantResultCode.POLICY_DENIED)
            self._used.add(grant_id)
            return GrantConsumeResult(True, GrantResultCode.CONSUMED)

    def cancel(self, grant_id: str) -> None:
        with self._lock:
            if grant_id in self._grants and grant_id not in self._used:
                self._cancelled.add(grant_id)

    def clear(self) -> None:
        with self._lock:
            self._grants.clear(); self._used.clear(); self._cancelled.clear()
