from __future__ import annotations
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from enum import Enum


class AuditEventType(str, Enum):
    PROPOSAL_CREATED = "proposal_created"
    POLICY_ALLOWED_READ_ONLY = "policy_allowed_read_only"
    POLICY_CONFIRMATION_REQUIRED = "policy_confirmation_required"
    POLICY_DENIED = "policy_denied"
    CONFIRMATION_SHOWN = "confirmation_shown"
    CONFIRMATION_APPROVED = "confirmation_approved"
    CONFIRMATION_CANCELLED = "confirmation_cancelled"
    CONFIRMATION_CLOSED = "confirmation_closed"
    CONFIRMATION_REVISE_REQUESTED = "confirmation_revise_requested"
    GRANT_ISSUED = "grant_issued"
    GRANT_CONSUMED = "grant_consumed"
    GRANT_REJECTED = "grant_rejected"
    GRANT_EXPIRED = "grant_expired"
    GRANT_CANCELLED = "grant_cancelled"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    VERIFICATION_SUCCEEDED = "verification_succeeded"
    VERIFICATION_FAILED = "verification_failed"
    AUDIT_WRITE_FAILED = "audit_write_failed"
    POWERSHELL_READ_STARTED = "powershell_read_started"
    POWERSHELL_READ_SUCCEEDED = "powershell_read_succeeded"
    POWERSHELL_READ_CANCELLED = "powershell_read_cancelled"
    POWERSHELL_READ_TIMEOUT = "powershell_read_timeout"
    POWERSHELL_READ_FAILED = "powershell_read_failed"
    POWERSHELL_READ_INVALID_OUTPUT = "powershell_read_invalid_output"
    POWERSHELL_EXECUTION_STARTED = "powershell_execution_started"
    POWERSHELL_EXECUTION_SUCCEEDED = "powershell_execution_succeeded"
    POWERSHELL_EXECUTION_FAILED = "powershell_execution_failed"
    POWERSHELL_EXECUTION_CANCELLED = "powershell_execution_cancelled"
    POWERSHELL_EXECUTION_TIMED_OUT = "powershell_execution_timed_out"
    POWERSHELL_VERIFICATION_SUCCEEDED = "powershell_verification_succeeded"
    POWERSHELL_VERIFICATION_FAILED = "powershell_verification_failed"
    POWERSHELL_TEMP_CLEANUP_FAILED = "powershell_temp_cleanup_failed"
    ELEVATION_REQUESTED = "elevation_requested"
    ELEVATION_ENVELOPE_CREATED = "elevation_envelope_created"
    ELEVATION_LAUNCH_STARTED = "elevation_launch_started"
    ELEVATION_UAC_CANCELLED = "elevation_uac_cancelled"
    ELEVATION_BROKER_STARTED = "elevation_broker_started"
    ELEVATION_VALIDATION_REJECTED = "elevation_validation_rejected"
    ELEVATION_EXECUTION_STARTED = "elevation_execution_started"
    ELEVATION_EXECUTION_SUCCEEDED = "elevation_execution_succeeded"
    ELEVATION_EXECUTION_FAILED = "elevation_execution_failed"
    ELEVATION_RESULT_RECEIVED = "elevation_result_received"
    ELEVATION_VERIFICATION_SUCCEEDED = "elevation_verification_succeeded"
    ELEVATION_VERIFICATION_FAILED = "elevation_verification_failed"
    ELEVATION_CLEANUP_FAILED = "elevation_cleanup_failed"


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    result_code: str = "ok"
    task_id: str = ""
    proposal_id: str = ""
    proposal_fingerprint: str = ""
    grant_id: str = ""
    tool_name: str = ""
    tool_version: str = ""
    operation: str = ""
    side_effect: str = ""
    confirmation_type: str = ""
    requires_admin: bool = False
    reversible: bool = False
    timestamp: str = ""
    script_sha256: str = ""
    timeout_seconds: float | None = None
    exit_code: int | None = None
    verification_result: str = ""
    item_count: int | None = None
    request_id: str = ""
    template_id: str = ""
    template_version: str = ""

    def __post_init__(self):
        if self.event_type not in {item.value for item in AuditEventType}:
            raise ValueError("invalid_audit_event")


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._lock = threading.Lock()
    def write(self, event: AuditEvent) -> None:
        with self._lock:
            self.events.append(event)


class NullAuditSink:
    def write(self, event: AuditEvent) -> bool:
        return True


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
    def write(self, event: AuditEvent) -> bool:
        record = {key: value for key, value in event.__dict__.items() if value != ""}
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stream.flush()
        except Exception:
            return False
        return True
