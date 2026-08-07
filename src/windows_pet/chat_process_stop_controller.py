from __future__ import annotations
import secrets
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal

from .action_confirmation_dialog import ActionConfirmationDialog
from .audit_log import NullAuditSink
from .confirmation_gate import ConfirmationGate
from .process_stop import (PowerShellExecutionProposalFactory, PowerShellExecutionRunner,
                           PowerShellExecutionStatus, ProcessIdentityResolver,
                           ProcessValidationCode, STOP_PROCESS_CONTRACT)

class ProcessStopWorker(QObject):
    finished = Signal(object)
    def __init__(self, executor, grant_id, proposal, identity, cancel):
        super().__init__(); self.executor, self.grant_id, self.proposal, self.identity, self.cancel = executor, grant_id, proposal, identity, cancel
    def run(self): self.finished.emit(self.executor.execute(self.grant_id, self.proposal, self.identity, self.cancel))

class ChatProcessStopController:
    """Confirmation remains on the UI thread; process control never does."""
    def __init__(self, complete, parent=None, audit=None, resolver=None, proposal_factory=None, confirmation_gate=None, executor=None, dialog_factory=ActionConfirmationDialog):
        self.complete, self.parent, self.audit = complete, parent, audit or NullAuditSink()
        self.resolver = resolver or ProcessIdentityResolver(); self.proposal_factory = proposal_factory or PowerShellExecutionProposalFactory()
        self.gate = confirmation_gate or ConfirmationGate(audit=self.audit); self.executor = executor or PowerShellExecutionRunner(self.gate.grants, self.resolver, audit=self.audit)
        self.dialog_factory = dialog_factory; self._cancel = Event(); self._grant_id = None; self._thread = self._worker = None
    @property
    def is_busy(self): return self._thread is not None
    def cancel(self):
        self._cancel.set()
        if self._grant_id: self.gate.grants.cancel(self._grant_id)
        self.executor.cancel()
    def request(self, request):
        if self.is_busy: return False
        self._cancel.clear(); identity = self.resolver.resolve(request.process_id)
        if identity is None: self.complete("対象のプロセスは見つかりませんでした。"); return False
        code = self.resolver.validate(identity, request.expected_process_name)
        if code is ProcessValidationCode.PROTECTED: self.complete("このプロセスは WindowsPet から終了できません。"); return False
        if code is not ProcessValidationCode.OK: self.complete("確認後にプロセス情報が変わったため、終了しませんでした。"); return False
        proposal = self.proposal_factory.create(secrets.token_urlsafe(12), identity); _, session = self.gate.prepare(STOP_PROCESS_CONTRACT, proposal)
        if session is None: self.complete("このプロセスは終了できません。"); return False
        dialog = self.dialog_factory(proposal, session, parent=self.parent); dialog.exec()
        result = self.gate.decide(STOP_PROCESS_CONTRACT, proposal, dialog.response)
        if result.grant is None: self.complete("プロセスの終了をキャンセルしました。"); return False
        self._grant_id = result.grant.grant_id; self._thread = QThread(self.parent); self._worker = ProcessStopWorker(self.executor, self._grant_id, proposal, identity, self._cancel)
        self._worker.moveToThread(self._thread); self._thread.started.connect(self._worker.run); self._worker.finished.connect(self._finished); self._worker.finished.connect(self._thread.quit); self._thread.finished.connect(self._thread_finished); self._thread.start(); return True
    def _finished(self, outcome):
        messages = {PowerShellExecutionStatus.SUCCEEDED:"プロセスを終了しました。", PowerShellExecutionStatus.CANCELLED:"プロセスの終了をキャンセルしました。", PowerShellExecutionStatus.VERIFICATION_FAILED:"終了後の確認で対象プロセスが残っていることを確認できませんでした。"}
        self.complete(messages.get(outcome.status, "プロセスを終了できませんでした。")); self._grant_id = None
    def _thread_finished(self):
        thread, worker = self._thread, self._worker; self._thread = self._worker = None
        if worker is not None: worker.deleteLater()
        if thread is not None: thread.deleteLater()
    def shutdown(self):
        self.cancel()
        if self._thread is not None:
            self._thread.quit()
            if not self._thread.wait(3000):
                self._thread.terminate(); self._thread.wait(1000)
        self._grant_id = None
