from __future__ import annotations

import secrets
from threading import Event

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from .action_confirmation_dialog import ActionConfirmationDialog
from .audit_log import NullAuditSink
from .confirmation_gate import ConfirmationGate
from .service_restart import (RESTART_SERVICE_CONTRACT, ServiceIdentityResolver,
                              ServiceResolutionCode, ServiceRestartProposalFactory,
                              ServiceRestartRunner, ServiceRestartStatus)


class ServiceRestartResolutionWorker(QObject):
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
            identity = self.resolver.resolve(self.request.service_query, list(self.request.snapshot))
            code = self.resolver.last_code if identity is None else self.resolver.validate(identity)
            if self.cancel.is_set():
                self.finished.emit(None, "cancelled")
            else:
                self.finished.emit(identity, code)
        except Exception:
            self.finished.emit(None, ServiceResolutionCode.NOT_FOUND)


class ServiceRestartWorker(QObject):
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
            outcome = type("Outcome", (), {"status": ServiceRestartStatus.FAILED})()
        self.finished.emit(outcome)


class ChatServiceRestartController(QObject):
    """Owns the confirmed service-restart lifecycle without GUI-thread I/O."""
    def __init__(self, complete, parent=None, audit=None, resolver=None, proposal_factory=None,
                 confirmation_gate=None, executor=None, dialog_factory=ActionConfirmationDialog,
                 resolver_worker_factory=ServiceRestartResolutionWorker,
                 execution_worker_factory=ServiceRestartWorker, thread_factory=QThread):
        super().__init__(parent)
        self.complete, self.audit = complete, audit or NullAuditSink()
        self.resolver = resolver or ServiceIdentityResolver()
        self.proposal_factory = proposal_factory or ServiceRestartProposalFactory()
        self.gate = confirmation_gate or ConfirmationGate(audit=self.audit)
        self.executor = executor or ServiceRestartRunner(self.gate.grants, self.resolver, audit=self.audit)
        self.dialog_factory = dialog_factory
        self.resolver_worker_factory, self.execution_worker_factory, self.thread_factory = resolver_worker_factory, execution_worker_factory, thread_factory
        self._cancel, self._grant_id, self._busy = Event(), None, False
        self.resolution_thread = self.resolution_worker = None
        self.execution_thread = self.execution_worker = None
        self._retired_threads = []

    @property
    def is_busy(self): return self._busy

    def request(self, request):
        if self._busy or not getattr(request, "snapshot", ()):
            return False
        self._busy = True; self._cancel.clear()
        reset = getattr(self.executor, "reset_cancel", None)
        if reset: reset()
        thread = self.resolution_thread = self.thread_factory(self)
        worker = self.resolution_worker = self.resolver_worker_factory(self.resolver, request, self._cancel)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.finished.connect(self._resolved); worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater); thread.finished.connect(self._on_resolution_thread_finished)
        thread.start(); return True

    def cancel(self):
        self._cancel.set()
        if self._grant_id: self.gate.grants.cancel(self._grant_id)
        cancel = getattr(self.executor, "cancel", None)
        if cancel: cancel()

    @Slot(object, object)
    def _resolved(self, identity, code):
        if not self._busy: return
        if code == "cancelled" or self._cancel.is_set(): self._finish("サービスの再起動をキャンセルしました。"); return
        if code is ServiceResolutionCode.PROTECTED: self._finish("このサービスは WindowsPet から再起動できません。"); return
        if code is ServiceResolutionCode.ADMIN_REQUIRED: self._finish("この操作には管理者権限が必要なため、実行は開始できません。"); return
        if code is ServiceResolutionCode.NOT_FOUND or identity is None: self._finish("対象のサービスが見つからなかったため、再起動しませんでした。"); return
        if code is not ServiceResolutionCode.MATCHED: self._finish("確認後に対象サービスの状態が変わったため、再起動しませんでした。"); return
        proposal = self.proposal_factory.create(secrets.token_urlsafe(12), identity)
        _, session = self.gate.prepare(RESTART_SERVICE_CONTRACT, proposal)
        if session is None: self._finish("このサービスは再起動できません。"); return
        dialog = self.dialog_factory(proposal, session, parent=self.parent()); dialog.exec()
        result = self.gate.decide(RESTART_SERVICE_CONTRACT, proposal, dialog.response)
        if result.grant is None: self._finish("サービスの再起動をキャンセルしました。"); return
        if self._cancel.is_set():
            self.gate.grants.cancel(result.grant.grant_id); self._finish("サービスの再起動をキャンセルしました。"); return
        self._grant_id = result.grant.grant_id
        thread = self.execution_thread = self.thread_factory(self)
        worker = self.execution_worker = self.execution_worker_factory(self.executor, self._grant_id, proposal, identity, self._cancel)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.finished.connect(self._finished); worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater); thread.finished.connect(self._on_execution_thread_finished)
        thread.start()

    @Slot(object)
    def _finished(self, outcome):
        messages = {ServiceRestartStatus.SUCCEEDED: "サービスを再起動しました。", ServiceRestartStatus.CANCELLED: "サービスの再起動をキャンセルしました。", ServiceRestartStatus.TIMED_OUT: "サービスの再起動がタイムアウトしました。", ServiceRestartStatus.VERIFICATION_FAILED: "再起動後の確認でサービスが Running ではなかったため、再起動は完了しませんでした。"}
        self._finish(messages.get(outcome.status, "サービスを再起動できませんでした。"))

    @Slot()
    def _on_resolution_thread_finished(self):
        self._cleanup_resolution(self.sender())
    @Slot()
    def _on_execution_thread_finished(self):
        self._cleanup_execution(self.sender())
    def _cleanup_resolution(self, thread):
        if self.resolution_thread is thread:
            self._retired_threads.append(thread)
            self.resolution_thread = self.resolution_worker = None
    def _cleanup_execution(self, thread):
        if self.execution_thread is thread:
            self._retired_threads.append(thread)
            self.execution_thread = self.execution_worker = None
    def _finish(self, text):
        if self._busy:
            self._busy = False; self._grant_id = None; self.complete(text)
    def shutdown(self):
        self.cancel()
        for thread in (self.resolution_thread, self.execution_thread):
            if thread is not None and thread.isRunning(): thread.wait(6000)
        self._busy = False; self._grant_id = None
