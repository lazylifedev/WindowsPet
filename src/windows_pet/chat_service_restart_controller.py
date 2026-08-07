from __future__ import annotations

import secrets
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .action_confirmation_dialog import ActionConfirmationDialog
from .audit_log import NullAuditSink
from .confirmation_gate import ConfirmationGate
from .service_restart import (RESTART_SERVICE_CONTRACT, ServiceIdentityResolver,
                              ServiceResolutionCode, ServiceRestartProposalFactory,
                              ServiceRestartRunner, ServiceRestartStatus)


class ServiceRestartResolutionThread(QThread):
    """Resolve a service entirely in the thread's ``run`` method."""

    result_ready = Signal(object, object)

    def __init__(self, resolver, request, cancel, parent=None):
        super().__init__(parent)
        self.resolver = resolver
        self.request = request
        self.cancel = cancel

    def run(self):
        if self.cancel.is_set():
            self.result_ready.emit(None, "cancelled")
            return
        try:
            snapshot = list(self.request.snapshot)
            identity = self.resolver.resolve(self.request.service_query, snapshot)
            # Validate against the same read-only inspection snapshot. A second
            # live inspection here could independently turn a found service
            # into not_found.
            code = (self.resolver.last_code if identity is None
                    else self.resolver.validate(identity, snapshot))
            if self.cancel.is_set():
                self.result_ready.emit(None, "cancelled")
            else:
                self.result_ready.emit(identity, code)
        except Exception:
            self.result_ready.emit(None, ServiceResolutionCode.NOT_FOUND)


class ServiceRestartExecutionThread(QThread):
    """Execute one already-confirmed restart without a cross-thread QObject."""

    result_ready = Signal(object)

    def __init__(self, executor, grant_id, proposal, identity, cancel, parent=None):
        super().__init__(parent)
        self.executor = executor
        self.grant_id = grant_id
        self.proposal = proposal
        self.identity = identity
        self.cancel = cancel

    def run(self):
        try:
            outcome = self.executor.execute(
                self.grant_id, self.proposal, self.identity, self.cancel
            )
        except Exception:
            outcome = type("Outcome", (), {"status": ServiceRestartStatus.FAILED})()
        self.result_ready.emit(outcome)


class ChatServiceRestartController(QObject):
    """Owns the confirmed service-restart lifecycle without GUI-thread I/O."""

    def __init__(self, complete, parent=None, audit=None, resolver=None,
                 proposal_factory=None, confirmation_gate=None, executor=None,
                 dialog_factory=ActionConfirmationDialog,
                 resolution_thread_factory=ServiceRestartResolutionThread,
                 execution_thread_factory=ServiceRestartExecutionThread):
        super().__init__(parent)
        self.complete = complete
        self.audit = audit or NullAuditSink()
        self.resolver = resolver or ServiceIdentityResolver()
        self.proposal_factory = proposal_factory or ServiceRestartProposalFactory()
        self.gate = confirmation_gate or ConfirmationGate(audit=self.audit)
        self.executor = executor or ServiceRestartRunner(
            self.gate.grants, self.resolver, audit=self.audit
        )
        self.dialog_factory = dialog_factory
        self.resolution_thread_factory = resolution_thread_factory
        self.execution_thread_factory = execution_thread_factory
        self._cancel = Event()
        self._grant_id = None
        self._busy = False
        self._execution_outcome = None
        self.resolution_thread = None
        self.execution_thread = None

    @property
    def is_busy(self):
        return self._busy

    def request(self, request):
        if self._busy or not getattr(request, "snapshot", ()):
            return False
        self._busy = True
        self._cancel.clear()
        reset = getattr(self.executor, "reset_cancel", None)
        if reset:
            reset()

        thread = self.resolution_thread = self.resolution_thread_factory(
            self.resolver, request, self._cancel, self
        )
        thread.result_ready.connect(self._resolved)
        thread.finished.connect(self._on_resolution_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        return True

    def cancel(self):
        """Only request cancellation; the active thread owns child cleanup."""
        self._cancel.set()
        if self._grant_id:
            self.gate.grants.cancel(self._grant_id)
        cancel = getattr(self.executor, "cancel", None)
        if cancel:
            cancel()

    @Slot(object, object)
    def _resolved(self, identity, code):
        if not self._busy:
            return
        if code == "cancelled" or self._cancel.is_set():
            self._finish("サービスの再起動をキャンセルしました。")
            return
        if code is ServiceResolutionCode.PROTECTED:
            self._finish("このサービスは WindowsPet から再起動できません。")
            return
        if code is ServiceResolutionCode.ADMIN_REQUIRED:
            self._finish("この操作には管理者権限が必要なため、実行を開始できませんでした。")
            return
        if code is ServiceResolutionCode.NOT_FOUND or identity is None:
            self._finish("対象のサービスが見つからなかったため、再起動しませんでした。")
            return
        if code is not ServiceResolutionCode.MATCHED:
            self._finish("確認後に対象サービスの状態が変わったため、再起動しませんでした。")
            return

        proposal = self.proposal_factory.create(secrets.token_urlsafe(12), identity)
        _, session = self.gate.prepare(RESTART_SERVICE_CONTRACT, proposal)
        if session is None:
            self._finish("このサービスは再起動できません。")
            return
        dialog = self.dialog_factory(proposal, session, parent=self.parent())
        dialog.exec()
        result = self.gate.decide(RESTART_SERVICE_CONTRACT, proposal, dialog.response)
        if result.grant is None:
            self._finish("サービスの再起動をキャンセルしました。")
            return
        if self._cancel.is_set():
            self.gate.grants.cancel(result.grant.grant_id)
            self._finish("サービスの再起動をキャンセルしました。")
            return

        self._grant_id = result.grant.grant_id
        thread = self.execution_thread = self.execution_thread_factory(
            self.executor, self._grant_id, proposal, identity, self._cancel, self
        )
        thread.result_ready.connect(self._finished)
        thread.finished.connect(self._on_execution_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @Slot(object)
    def _finished(self, outcome):
        # Result delivery and QThread destruction are separate events. Keep the
        # outcome until thread.finished so the strong thread reference remains
        # valid through the complete execution lifecycle.
        self._execution_outcome = outcome

    @Slot()
    def _on_resolution_thread_finished(self):
        thread = self.sender()
        if self.resolution_thread is thread:
            self.resolution_thread = None

    @Slot()
    def _on_execution_thread_finished(self):
        thread = self.sender()
        if self.execution_thread is not thread:
            return
        self.execution_thread = None
        outcome, self._execution_outcome = self._execution_outcome, None
        self._grant_id = None
        if outcome is not None:
            messages = {
                ServiceRestartStatus.SUCCEEDED: "サービスを再起動しました。",
                ServiceRestartStatus.CANCELLED: "サービスの再起動をキャンセルしました。",
                ServiceRestartStatus.TIMED_OUT: "サービスの再起動がタイムアウトしました。",
                ServiceRestartStatus.VERIFICATION_FAILED: "再起動後の確認でサービスが Running ではなかったため、再起動は完了しませんでした。",
            }
            self._finish(messages.get(outcome.status, "サービスを再起動できませんでした。"))

    def _finish(self, text):
        if self._busy:
            self._busy = False
            self._grant_id = None
            self.complete(text)

    def shutdown(self):
        """Request cooperative shutdown; bounded waits are reserved for exit."""
        self.cancel()
        for thread in (self.resolution_thread, self.execution_thread):
            if thread is not None and thread.isRunning():
                thread.wait(6000)
        self._busy = False
        self._grant_id = None
