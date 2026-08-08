from __future__ import annotations

from pathlib import Path
from typing import Callable
from threading import Event

from ..audit_log import AuditEvent, NullAuditSink
from .envelope import ElevationEnvelopeFactory, write_envelope_file
from .models import ElevationClientOutcome, ElevationRequest, ElevationStatus


class ElevationBrokerClient:
    """Main-side envelope/launch/result/verification boundary."""
    def __init__(self, launcher, *, audit=None, envelope_directory: Path | None = None):
        self.launcher = launcher
        self.audit = audit or NullAuditSink()
        self.envelope_directory = envelope_directory

    def execute(self, request: ElevationRequest, broker_path, *, verifier: Callable | None = None,
                cancel_event: Event | None = None):
        if cancel_event is None:
            cancel_event = Event()
        envelope = ElevationEnvelopeFactory.create(request)
        payload = write_envelope_file(envelope, self.envelope_directory)
        self.audit.write(AuditEvent("elevation_requested", request_id=request.request_id,
                                   proposal_id=request.proposal_id, proposal_fingerprint=request.proposal_fingerprint,
                                   grant_id=request.grant_id, operation=request.operation_id,
                                   template_id=request.template_id, template_version=request.template_version,
                                   script_sha256=request.script_sha256))
        self.audit.write(AuditEvent("elevation_envelope_created", request_id=request.request_id,
                                   proposal_id=request.proposal_id, grant_id=request.grant_id,
                                   operation=request.operation_id, template_id=request.template_id,
                                   template_version=request.template_version, script_sha256=request.script_sha256))
        try:
            self.audit.write(AuditEvent("elevation_launch_started", request_id=request.request_id,
                                       proposal_id=request.proposal_id, grant_id=request.grant_id,
                                       operation=request.operation_id, script_sha256=request.script_sha256))
            outcome = self.launcher.launch(
                broker_path, payload.path, request.timeout_seconds, cancel_event,
                expected_envelope_sha256=payload.sha256,
            )
            if outcome.status is ElevationStatus.CANCELLED:
                code = outcome.reason
                event = "elevation_uac_cancelled" if code == "uac_cancelled" else "elevation_execution_failed"
                self.audit.write(AuditEvent(event, result_code=code, request_id=request.request_id,
                                           proposal_id=request.proposal_id, grant_id=request.grant_id,
                                           operation=request.operation_id, script_sha256=request.script_sha256))
                return ElevationClientOutcome(ElevationStatus.CANCELLED, code, outcome.result)
            result = outcome.result
            if result is None or result.request_id != request.request_id or result.operation_id != request.operation_id or result.script_sha256 != request.script_sha256:
                self.audit.write(AuditEvent("elevation_result_received", result_code="wrong_request_result",
                                           request_id=request.request_id, proposal_id=request.proposal_id,
                                           grant_id=request.grant_id, operation=request.operation_id,
                                           script_sha256=request.script_sha256))
                return ElevationClientOutcome(ElevationStatus.REJECTED, "wrong_request_result", result)
            self.audit.write(AuditEvent("elevation_result_received", result_code=result.result_code,
                                       request_id=result.request_id, proposal_id=request.proposal_id,
                                       grant_id=request.grant_id, operation=result.operation_id,
                                       script_sha256=result.script_sha256, exit_code=result.exit_code))
            if result.status is not ElevationStatus.SUCCEEDED:
                return ElevationClientOutcome(result.status, result.result_code, result)
            if verifier is None:
                return ElevationClientOutcome(ElevationStatus.REJECTED, "verification_required", result)
            try:
                verification = verifier(result)
            except Exception:
                verification = False
            if verification is False or verification is None:
                self.audit.write(AuditEvent("elevation_verification_failed", result_code="verification_failed",
                                           request_id=result.request_id, proposal_id=request.proposal_id,
                                           grant_id=request.grant_id, operation=result.operation_id,
                                           script_sha256=result.script_sha256, verification_result="failed"))
                return ElevationClientOutcome(ElevationStatus.FAILED, "verification_failed", result, "failed")
            verification_text = "running" if verification is True else str(verification)
            self.audit.write(AuditEvent("elevation_verification_succeeded", request_id=result.request_id,
                                       proposal_id=request.proposal_id, grant_id=request.grant_id,
                                       operation=result.operation_id, script_sha256=result.script_sha256,
                                       verification_result=verification_text))
            return ElevationClientOutcome(ElevationStatus.SUCCEEDED, "ok", result, verification_text)
        finally:
            # Broker normally consumes this file. This is a safe fallback for
            # launch cancellation or a launcher failure before process start.
            if not payload.cleanup():
                self.audit.write(AuditEvent("elevation_cleanup_failed", result_code="cleanup_failed",
                                           request_id=request.request_id, proposal_id=request.proposal_id,
                                           grant_id=request.grant_id, operation=request.operation_id))
