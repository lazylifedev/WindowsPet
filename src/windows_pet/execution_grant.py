from __future__ import annotations
import threading
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .action_models import ActionProposal


@dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    proposal_id: str
    proposal_fingerprint: str
    issued_at: datetime
    expires_at: datetime


class ExecutionGrantStore:
    """In-memory, atomic, one-time grant store."""
    def __init__(self, now=None, lifetime: timedelta = timedelta(seconds=90)):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lifetime = lifetime
        self._lock = threading.Lock()
        self._grants: dict[str, ExecutionGrant] = {}
        self._used: set[str] = set()

    def issue(self, proposal: ActionProposal) -> ExecutionGrant:
        with self._lock:
            issued = self.now()
            grant = ExecutionGrant(secrets.token_urlsafe(24), proposal.proposal_id, proposal.fingerprint, issued, issued + self.lifetime)
            self._grants[grant.grant_id] = grant
            return grant

    def consume(self, grant_id: str, proposal: ActionProposal) -> bool:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None or grant_id in self._used or self.now() >= grant.expires_at: return False
            if grant.proposal_id != proposal.proposal_id or grant.proposal_fingerprint != proposal.fingerprint: return False
            self._used.add(grant_id)
            return True

    def cancel(self, grant_id: str) -> None:
        with self._lock: self._used.add(grant_id)

    def clear(self) -> None:
        with self._lock:
            self._grants.clear(); self._used.clear()
