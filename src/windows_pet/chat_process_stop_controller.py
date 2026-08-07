from __future__ import annotations

import secrets
from threading import Event

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from .action_confirmation_dialog import ActionConfirmationDialog
from .audit_log import NullAuditSink
from .confirmation_gate import ConfirmationGate
from .process_stop import (PowerShellExecutionProposalFactory, PowerShellExecutionRunner,
                           PowerShellExecutionStatus, ProcessIdentityResolver,
                           ProcessValidationCode, STOP_PROCESS_CONTRACT)


class ProcessIdentityResolutionWorker(QObject):
    """Resolve and validate an identity away from the GUI event loop."""
    finished = Signal(object, object)

    def __init__(self, resolver, request, cancel):
        super().__init__()
        self.resolver, self.request, self.cancel = resolver, request, cancel

    @Slot()
    def run(self):
        if self.cancel.is_set():
            self.finished.emit(None, "cancelled")
            return
        try:
            identity = self.resolver.resolve(self.request.process_id)
            if self.cancel.is_set():
                self.finished.emit(None, "cancelled")
            elif identity is None:
                self.finished.emit(None, ProcessValidationCode.MISSING)
            else:
                self.finished.emit(identity, self.resolver.validate(identity, self.request.expected_process_name))
        except Exception:
            self.finished.emit(None, ProcessValidationCode.MISSING)


class ProcessStopWorker(QObject):
    finished = Signal(object)

    def __init__(self, executor, grant_id, proposal, identity, cancel):
        super().__init__()
        self.executor, self.grant_id, self.proposal = executor, grant_id, proposal
        self.identity, self.cancel = identity, cancel

    @Slot()
    def run(self):
        try:
            outcome = self.executor.execute(self.grant_id, self.proposal, self.identity, self.cancel)
        except Exception:
            outcome = type("Outcome", (), {"status": PowerShellExecutionStatus.FAILED})()
        self.finished.emit(outcome)


class ChatProcessStopController(QObject):
    """Owns the process-stop lifecycle; UI work stays on this object's thread."""
    def __init__(self, complete, parent=None, audit=None, resolver=None, proposal_factory=None,
                 confirmation_gate=None, executor=None, dialog_factory=ActionConfirmationDialog,
                 resolver_worker_factory=ProcessIdentityResolutionWorker,
                 execution_worker_factory=ProcessStopWorker, thread_factory=QThread):
        super().__init__(parent)
        self.complete, self.audit = complete, audit or NullAuditSink()
        self.resolver = resolver or ProcessIdentityResolver()
        self.proposal_factory = proposal_factory or PowerShellExecutionProposalFactory()
        self.gate = confirmation_gate or ConfirmationGate(audit=self.audit)
        self.executor = executor or PowerShellExecutionRunner(self.gate.grants, self.resolver, audit=self.audit)
        self.dialog_factory = dialog_factory
        self.resolver_worker_factory, self.execution_worker_factory, self.thread_factory = resolver_worker_factory, execution_worker_factory, thread_factory
        self._cancel = Event()
        self._grant_id = None
        self.resolution_thread = self.resolution_worker = None
        self.execution_thread = self.execution_worker = None
        self._busy = False

    @property
    def is_busy(self):
        return self._busy

    def request(self, request):
        if self._busy:
            return False
        self._busy = True
        self._cancel.clear()
        reset = getattr(self.executor, "reset_cancel", None)
        if reset is not None:
            reset()
        thread = self.resolution_thread = self.thread_factory(self)
        worker = self.resolution_worker = self.resolver_worker_factory(self.resolver, request, self._cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._resolved)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._cleanup_resolution_thread(thread))
        thread.start()
        return True

    def cancel(self):
        """Only request cancellation. Process cleanup is performed by its worker."""
        self._cancel.set()
        if self._grant_id:
            self.gate.grants.cancel(self._grant_id)
        cancel = getattr(self.executor, "cancel", None)
        if cancel is not None:
            cancel()

    @Slot(object, object)
    def _resolved(self, identity, code):
        if not self._busy:
            return
        if code == "cancelled" or self._cancel.is_set():
            self._finish("プロセスの終了をキャンセルしました。")
            return
        if identity is None or code is ProcessValidationCode.MISSING:
            self._finish("対象のプロセスは見つかりませんでした。")
            return
        if code is ProcessValidationCode.PROTECTED:
            self._finish("このプロセスは WindowsPet から終了できません。")
            return
        if code is not ProcessValidationCode.OK:
            self._finish("確認後にプロセス情報が変わったため、終了しませんでした。")
            return
        proposal = self.proposal_factory.create(secrets.token_urlsafe(12), identity)
        _, session = self.gate.prepare(STOP_PROCESS_CONTRACT, proposal)
        if session is None:
            self._finish("このプロセスは終了できません。")
            return
        dialog = self.dialog_factory(proposal, session, parent=self.parent())
        dialog.exec()
        result = self.gate.decide(STOP_PROCESS_CONTRACT, proposal, dialog.response)
        if result.grant is None:
            self._finish("プロセスの終了をキャンセルしました。")
            return
        if self._cancel.is_set():
            self.gate.grants.cancel(result.grant.grant_id)
            self._finish("プロセスの終了をキャンセルしました。")
            return
        self._grant_id = result.grant.grant_id
        thread = self.execution_thread = self.thread_factory(self)
        worker = self.execution_worker = self.execution_worker_factory(self.executor, self._grant_id, proposal, identity, self._cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._finished)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._cleanup_execution_thread(thread))
        thread.start()

    @Slot(object)
    def _finished(self, outcome):
        messages = {
            PowerShellExecutionStatus.SUCCEEDED: "プロセスを終了しました。",
            PowerShellExecutionStatus.CANCELLED: "プロセスの終了をキャンセルしました。",
            PowerShellExecutionStatus.VERIFICATION_FAILED: "終了後の確認で対象プロセスが残っていることを確認しました。",
        }
        self._finish(messages.get(outcome.status, "プロセスを終了できませんでした。"))

    def _cleanup_resolution_thread(self, thread):
        if self.resolution_thread is thread:
            self.resolution_thread = self.resolution_worker = None

    def _cleanup_execution_thread(self, thread):
        if self.execution_thread is thread:
            self.execution_thread = self.execution_worker = None

    def _finish(self, text):
        if self._busy:
            self._busy = False
            self._grant_id = None
            self.complete(text)

    def shutdown(self):
        self.cancel()
        # Resolver has a 3s subprocess bound; execution checks cancellation every 100ms
        # and bounds terminate/kill cleanup to four seconds.  Never force-terminate Qt.
        for thread in (self.resolution_thread, self.execution_thread):
            if thread is not None and thread.isRunning():
                thread.wait(6000)
        self._busy = False
        self._grant_id = None
