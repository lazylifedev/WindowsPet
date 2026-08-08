from __future__ import annotations

from PySide6.QtCore import QObject

from .action_confirmation_dialog import ActionConfirmationDialog
from .confirmation_gate import ConfirmationGate, ConfirmationResultCode
from .file_rename import FileRenameExecutor, FileRenameProposalFactory, FileRenameValidator, FILE_RENAME_CONTRACT
from .audit_log import AuditEvent, NullAuditSink
from .reflection import Experience, ReflectionPipeline
from datetime import datetime, timezone
import secrets


class ChatFileRenameController(QObject):
    """GUI handoff for file rename; no filesystem mutation occurs before approval."""

    def __init__(self, complete, parent=None, audit=None, validator=None, confirmation_dialog_factory=ActionConfirmationDialog,
                 proposal_factory=FileRenameProposalFactory, confirmation_gate=None, executor=None, reflection=None):
        super().__init__(parent)
        self.complete = complete
        self.audit = audit or NullAuditSink()
        self.validator = validator or FileRenameValidator()
        self.confirmation_dialog_factory = confirmation_dialog_factory
        self.proposal_factory = proposal_factory
        self.gate = confirmation_gate or ConfirmationGate(audit=audit)
        self.executor = executor or FileRenameExecutor(self.gate.grants, self.validator, audit=audit)
        self.reflection = reflection or ReflectionPipeline()
        self.last_reflection = None
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def request(self, request) -> bool:
        if self._busy or request is None or request.source_path is None:
            return False
        self._busy = True
        try:
            snapshot, code = self.validator.snapshot(request.source_path, request.new_name)
            if snapshot is None:
                self._finish(f"ファイル名を変更できませんでした（{code.value}）。")
                return False
            proposal = self.proposal_factory().create(secrets.token_urlsafe(12), snapshot)
            self.audit.write(AuditEvent("file_rename_proposed", task_id=proposal.task_id,
                                        proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint,
                                        tool_name=proposal.tool_name, tool_version=proposal.tool_version,
                                        operation=proposal.operation, side_effect=proposal.side_effect.value,
                                        confirmation_type=proposal.confirmation_type.value, reversible=True))
            _, session = self.gate.prepare(FILE_RENAME_CONTRACT, proposal)
            if session is None:
                self._finish("このファイル名変更は実行できません。")
                return False
            dialog = self.confirmation_dialog_factory(proposal, session, parent=self.parent())
            dialog.exec()
            result = self.gate.decide(FILE_RENAME_CONTRACT, proposal, dialog.response)
            if result.grant is None:
                self._finish("ファイル名を変更しませんでした。" if result.reason is not ConfirmationResultCode.EXPIRED else "確認の有効期限が切れました。もう一度依頼してください。")
                return False
            self.audit.write(AuditEvent("file_rename_confirmed", task_id=proposal.task_id,
                                        proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint,
                                        grant_id=result.grant.grant_id, tool_name=proposal.tool_name,
                                        tool_version=proposal.tool_version, operation=proposal.operation,
                                        side_effect=proposal.side_effect.value,
                                        confirmation_type=proposal.confirmation_type.value, reversible=True))
            outcome = self.executor.execute(result.grant.grant_id, proposal, snapshot)
            if outcome.success:
                experience = Experience(proposal.task_id, "rename_file", "rename_file", "local file",
                                         datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
                                         "succeeded", "verified")
                self.reflection.record_experience(experience)
                self.last_reflection = self.reflection.reflect(experience)
            self._finish("ファイル名を変更しました。" if outcome.success else "ファイル名を変更できませんでした。")
            return outcome.success
        finally:
            self._busy = False

    def cancel(self) -> None:
        self._busy = False

    def shutdown(self) -> None:
        self.cancel()

    def _finish(self, text: str) -> None:
        self.complete(text)
