from __future__ import annotations
import secrets
from threading import Event

from .action_confirmation_dialog import ActionConfirmationDialog
from .audit_log import NullAuditSink
from .confirmation_gate import ConfirmationGate
from .process_stop import (PowerShellExecutionProposalFactory, PowerShellExecutionRunner,
                           PowerShellExecutionStatus, ProcessIdentityResolver,
                           ProcessValidationCode, STOP_PROCESS_CONTRACT)

class ChatProcessStopController:
    """Local handoff for the only process-control capability.

    The model supplies a PID/name request; the controller resolves its current
    identity locally and never accepts a script or process path from the model.
    """
    def __init__(self, complete, parent=None, audit=None, resolver=None, proposal_factory=None,
                 confirmation_gate=None, executor=None, dialog_factory=ActionConfirmationDialog):
        self.complete, self.parent, self.audit = complete, parent, audit or NullAuditSink()
        self.resolver = resolver or ProcessIdentityResolver(); self.proposal_factory = proposal_factory or PowerShellExecutionProposalFactory()
        self.gate = confirmation_gate or ConfirmationGate(audit=self.audit)
        self.executor = executor or PowerShellExecutionRunner(self.gate.grants, self.resolver, audit=self.audit)
        self.dialog_factory = dialog_factory; self._cancel = Event(); self._grant_id = None

    def cancel(self):
        self._cancel.set()
        if self._grant_id: self.gate.grants.cancel(self._grant_id)

    def request(self, request):
        self._cancel.clear(); identity = self.resolver.resolve(request.process_id)
        if identity is None: self.complete("対象のプロセスは見つかりませんでした。"); return False
        code = self.resolver.validate(identity, request.expected_process_name)
        if code is ProcessValidationCode.PROTECTED: self.complete("このプロセスはWindowsPetから終了できません。"); return False
        if code is not ProcessValidationCode.OK: self.complete("確認後にプロセス情報が変わったため、終了しませんでした。"); return False
        proposal = self.proposal_factory.create(secrets.token_urlsafe(12), identity)
        _, session = self.gate.prepare(STOP_PROCESS_CONTRACT, proposal)
        if session is None: self.complete("このプロセスは終了できません。"); return False
        dialog = self.dialog_factory(proposal, session, parent=self.parent); dialog.exec()
        result = self.gate.decide(STOP_PROCESS_CONTRACT, proposal, dialog.response)
        if result.grant is None: self.complete("プロセスの終了をキャンセルしました。"); return False
        self._grant_id = result.grant.grant_id
        outcome = self.executor.execute(result.grant.grant_id, proposal, identity, self._cancel)
        self._grant_id = None
        messages = {PowerShellExecutionStatus.SUCCEEDED: "プロセスを終了しました。", PowerShellExecutionStatus.CANCELLED: "プロセスの終了をキャンセルしました。", PowerShellExecutionStatus.VERIFICATION_FAILED: "終了処理は完了しましたが、対象プロセスが残っていることを確認しました。"}
        self.complete(messages.get(outcome.status, "プロセスを終了できませんでした。")); return outcome.status is PowerShellExecutionStatus.SUCCEEDED
