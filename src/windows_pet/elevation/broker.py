from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable, Mapping

from ..audit_log import AuditEvent, NullAuditSink
from .envelope import MAX_ENVELOPE_BYTES, default_elevation_directory, read_envelope_file
from .models import ElevationEnvelope, ElevationReason, ElevationResult, ElevationStatus
from .validation import BrokerValidationError, EnvelopeValidator


@dataclass(frozen=True)
class DispatchOutcome:
    status: ElevationStatus
    result_code: str
    exit_code: int | None = 0
    verification_hint: str = ""


class OneShotClaimStore:
    """Cross-process one-shot claims backed by exclusive file creation."""
    def __init__(self, directory: Path | None = None):
        local = os.environ.get("LOCALAPPDATA")
        self.directory = Path(directory) if directory else (Path(local) / "WindowsPet" / "elevation" / "claims" if local else Path(tempfile.gettempdir()) / "WindowsPet" / "elevation" / "claims")
        self.directory = self.directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    @staticmethod
    def _key(grant_id: str, nonce: str) -> str:
        return hashlib.sha256(f"grant\0{grant_id}".encode()).hexdigest(), hashlib.sha256(f"nonce\0{nonce}".encode()).hexdigest()

    def claim(self, grant_id: str, nonce: str) -> str:
        if not isinstance(grant_id, str) or not grant_id or not isinstance(nonce, str) or not nonce:
            return "grant_invalid"
        grant_key, nonce_key = self._key(grant_id, nonce)
        paths = (self.directory / f"grant-{grant_key}.claim", self.directory / f"nonce-{nonce_key}.claim")
        handles = []
        try:
            for path in paths:
                try:
                    handles.append(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                except FileExistsError:
                    return "grant_reused" if path.name.startswith("grant-") else "replayed_nonce"
            record = json.dumps({"grant_sha256": grant_key, "nonce_sha256": nonce_key}, separators=(",", ":")).encode()
            for handle in handles:
                os.write(handle, record)
            return "claimed"
        finally:
            for handle in handles:
                os.close(handle)
            # If the second claim lost a race, remove only our just-created
            # grant marker. The pre-existing marker remains authoritative.
            if len(handles) == 1:
                try:
                    paths[0].unlink(missing_ok=True)
                except OSError:
                    pass


class InMemoryClaimStore:
    """Test helper only; production BrokerEntryPoint defaults to file claims."""
    def __init__(self):
        self._lock = threading.Lock()
        self._grants, self._nonces = set(), set()

    def claim(self, grant_id, nonce):
        with self._lock:
            if grant_id in self._grants:
                return "grant_reused"
            if nonce in self._nonces:
                return "replayed_nonce"
            self._grants.add(grant_id); self._nonces.add(nonce)
            return "claimed"


class FakeElevatedExecutor:
    def __init__(self, *, exit_code: int = 0, delay_seconds: float = 0,
                 on_execute: Callable[[str, Mapping], None] | None = None):
        self.exit_code = exit_code
        self.delay_seconds = delay_seconds
        self.on_execute = on_execute
        self.execution_count = 0

    def execute(self, operation_id: str, parameters: dict, cancel_event: Event | None = None) -> DispatchOutcome:
        self.execution_count += 1
        if self.on_execute:
            self.on_execute(operation_id, parameters)
        deadline = time.monotonic() + self.delay_seconds
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                return DispatchOutcome(ElevationStatus.CANCELLED, "broker_execution_cancelled", None)
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if self.exit_code == 0:
            return DispatchOutcome(ElevationStatus.SUCCEEDED, "ok", 0, "execution_completed")
        return DispatchOutcome(ElevationStatus.FAILED, "nonzero_exit", self.exit_code)


class ElevatedOperationDispatcher:
    def __init__(self, executors: Mapping[str, object] | None = None):
        self.executors = dict(executors or {"restart_service": FakeElevatedExecutor()})

    def dispatch(self, envelope: ElevationEnvelope, cancel_event: Event | None = None) -> DispatchOutcome:
        executor = self.executors.get(envelope.operation_id)
        if executor is None:
            return DispatchOutcome(ElevationStatus.REJECTED, ElevationReason.WRONG_OPERATION.value, None)
        return executor.execute(envelope.operation_id, dict(envelope.parameters), cancel_event)


class BrokerEntryPoint:
    """Single-shot entry point: read, validate, claim, dispatch, return, exit."""
    def __init__(self, validator=None, dispatcher=None, claims=None, audit=None, clock=None,
                 envelope_root: Path | None = None):
        self.validator = validator or EnvelopeValidator()
        self.dispatcher = dispatcher or ElevatedOperationDispatcher()
        self.claims = claims or OneShotClaimStore()
        self.audit = audit or NullAuditSink()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.envelope_root = Path(envelope_root).resolve() if envelope_root else default_elevation_directory().resolve()

    @classmethod
    def production(cls, **kwargs):
        """Explicit production construction; the normal default stays Fake-safe."""
        from .executors import ElevatedRestartServiceExecutor
        from .broker import ElevatedOperationDispatcher
        kwargs["dispatcher"] = ElevatedOperationDispatcher({
            "restart_service": ElevatedRestartServiceExecutor(),
        })
        return cls(**kwargs)

    @staticmethod
    def result_path_for_envelope(envelope_path: Path) -> Path:
        path = Path(envelope_path)
        if not path.is_absolute() or not re.fullmatch(r"envelope-[0-9a-f]{40}\.json", path.name):
            raise ValueError("invalid_envelope_path")
        return path.with_name(path.stem + ".result.json")

    def _result(self, envelope, status, code, started, *, exit_code=None, hint=""):
        return ElevationResult(
            request_id=envelope.request_id if envelope else "invalid-request",
            operation_id=envelope.operation_id if envelope else "unknown",
            status=status,
            result_code=code,
            exit_code=exit_code,
            script_sha256=envelope.script_sha256 if envelope else "0" * 64,
            started_at=started,
            finished_at=self.clock(),
            verification_hint=hint,
        )

    def run(self, envelope_path: Path, *, result_path: Path | None = None,
            expected_envelope_sha256: str | None = None,
            cancel_event: Event | None = None) -> ElevationResult:
        started = self.clock()
        envelope = None
        try:
            if result_path is not None and Path(result_path) != self.result_path_for_envelope(Path(envelope_path)):
                raise ValueError("invalid_result_path")
            envelope, _ = read_envelope_file(Path(envelope_path), expected_sha256=expected_envelope_sha256, root=self.envelope_root)
            self.audit.write(AuditEvent("elevation_broker_started", request_id=envelope.request_id, proposal_id=envelope.proposal_id, proposal_fingerprint=envelope.proposal_fingerprint, grant_id=envelope.grant_id, operation=envelope.operation_id, template_id=envelope.template_id, template_version=envelope.template_version, script_sha256=envelope.script_sha256))
            try:
                self.validator.validate(envelope)
            except BrokerValidationError as error:
                self.audit.write(AuditEvent("elevation_validation_rejected", result_code=error.reason, request_id=envelope.request_id, proposal_id=envelope.proposal_id, grant_id=envelope.grant_id, operation=envelope.operation_id, template_id=envelope.template_id, template_version=envelope.template_version, script_sha256=envelope.script_sha256))
                return self._result(envelope, ElevationStatus.REJECTED, error.reason, started)
            if cancel_event and cancel_event.is_set():
                return self._result(envelope, ElevationStatus.CANCELLED, "cancelled_before_execution", started)
            claim = self.claims.claim(envelope.grant_id, envelope.nonce)
            if claim != "claimed":
                self.audit.write(AuditEvent("elevation_validation_rejected", result_code=claim, request_id=envelope.request_id, proposal_id=envelope.proposal_id, grant_id=envelope.grant_id, operation=envelope.operation_id, script_sha256=envelope.script_sha256))
                return self._result(envelope, ElevationStatus.REJECTED, claim, started)
            self.audit.write(AuditEvent("elevation_execution_started", request_id=envelope.request_id, proposal_id=envelope.proposal_id, grant_id=envelope.grant_id, operation=envelope.operation_id, template_id=envelope.template_id, template_version=envelope.template_version, script_sha256=envelope.script_sha256))
            outcome = self.dispatcher.dispatch(envelope, cancel_event)
            event = "elevation_execution_succeeded" if outcome.status is ElevationStatus.SUCCEEDED else "elevation_execution_failed"
            self.audit.write(AuditEvent(event, result_code=outcome.result_code, request_id=envelope.request_id, proposal_id=envelope.proposal_id, grant_id=envelope.grant_id, operation=envelope.operation_id, template_id=envelope.template_id, template_version=envelope.template_version, script_sha256=envelope.script_sha256, exit_code=outcome.exit_code))
            return self._result(envelope, outcome.status, outcome.result_code, started, exit_code=outcome.exit_code, hint=outcome.verification_hint)
        except Exception as error:
            code = error.reason if isinstance(error, BrokerValidationError) else "invalid_envelope"
            if envelope is not None:
                self.audit.write(AuditEvent("elevation_validation_rejected", result_code=code, request_id=envelope.request_id, proposal_id=envelope.proposal_id, grant_id=envelope.grant_id, operation=envelope.operation_id, script_sha256=envelope.script_sha256))
            return self._result(envelope, ElevationStatus.REJECTED, code, started)
        finally:
            try:
                Path(envelope_path).unlink(missing_ok=True)
            except OSError:
                if envelope is not None:
                    self.audit.write(AuditEvent("elevation_cleanup_failed", result_code="cleanup_failed", request_id=envelope.request_id, grant_id=envelope.grant_id, operation=envelope.operation_id))

    @staticmethod
    def write_result(result: ElevationResult, result_path: Path, *, root: Path | None = None) -> None:
        payload = {
            "request_id": result.request_id, "operation_id": result.operation_id,
            "status": result.status.value, "result_code": result.result_code,
            "exit_code": result.exit_code, "script_sha256": result.script_sha256,
            "started_at": result.started_at.isoformat(), "finished_at": result.finished_at.isoformat(),
            "verification_hint": result.verification_hint,
        }
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(data) > MAX_ENVELOPE_BYTES:
            raise ValueError("result_too_large")
        path = Path(result_path)
        if not path.is_absolute() or not re.fullmatch(r"envelope-[0-9a-f]{40}\.result\.json", path.name):
            raise ValueError("invalid_result_path")
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("invalid_result_path")
        if root is not None and path.parent.resolve() != Path(root).resolve():
            raise ValueError("result_path_boundary")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
