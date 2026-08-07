from __future__ import annotations

import secrets
from PySide6.QtCore import QObject, QThread

from .action_confirmation_dialog import ActionConfirmationDialog
from .application_candidate_resolver import (ApplicationCandidateResolver,
    ApplicationCandidateResolverWorker, CandidateResolutionStatus)
from .application_candidate_selection_dialog import ApplicationCandidateSelectionDialog
from .application_launch import (ApplicationLaunchExecutor, ApplicationLaunchProposalFactory,
    ApplicationLaunchStatus, ApplicationLaunchValidator, ApplicationLaunchWorker,
    APPLICATION_LAUNCH_CONTRACT)
from .audit_log import NullAuditSink
from .cancellation import CancellationToken
from .confirmation_gate import ConfirmationGate


class ChatApplicationLaunchController(QObject):
    """Owns both worker lifecycles for one confirmed chat launch."""
    def __init__(self, complete, parent=None, audit=None, resolver=None, validator=None):
        super().__init__(parent)
        self.complete = complete
        self.audit = audit or NullAuditSink()
        self.validator = validator or ApplicationLaunchValidator()
        self.resolver = resolver or ApplicationCandidateResolver(validator=self.validator)
        self.gate = ConfirmationGate(audit=self.audit)
        self.executor = ApplicationLaunchExecutor(self.gate.grants, validator=self.validator, audit=self.audit)
        self.resolver_thread = self.resolver_worker = self.resolver_token = None
        self.launch_thread = self.launch_worker = self.launch_token = None
        self._busy = False

    @property
    def is_busy(self):
        return self._busy

    def request(self, request):
        if self._busy:
            self.complete("別のアプリ起動確認を処理中です。")
            return False
        self._busy = True
        self.resolver_token = CancellationToken()
        self.complete("アプリの起動確認を準備しています。")
        thread = self.resolver_thread = QThread(self)
        worker = self.resolver_worker = ApplicationCandidateResolverWorker(self.resolver, request, self.resolver_token)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._resolved)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_resolver_thread(thread))
        thread.start()
        return True

    def cancel(self):
        if self.resolver_token is not None: self.resolver_token.cancel()
        if self.launch_token is not None: self.launch_token.cancel()

    def shutdown(self):
        self.cancel()
        for thread in (self.resolver_thread, self.launch_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2000)
        self._busy = False

    def _cleanup_resolver_thread(self, thread):
        if self.resolver_thread is thread:
            self.resolver_thread = self.resolver_worker = self.resolver_token = None

    def _cleanup_launch_thread(self, thread):
        if self.launch_thread is thread:
            self.launch_thread = self.launch_worker = self.launch_token = None

    def _finish(self, text):
        if not self._busy:
            return
        self._busy = False
        self.complete(text)

    def _resolved(self, outcome):
        if outcome.status is CandidateResolutionStatus.CANCELLED:
            self._finish("アプリの起動をキャンセルしました。")
            return
        if outcome.status is not CandidateResolutionStatus.SUCCESS or not outcome.candidates:
            self._finish("別のアプリ起動確認を処理中です。")
            return
        candidate = outcome.candidates[0]
        if len(outcome.candidates) > 1:
            dialog = ApplicationCandidateSelectionDialog(outcome.candidates, parent=self.parent())
            if dialog.exec() != dialog.Accepted or dialog.selected_candidate is None:
                self._finish("アプリの起動をキャンセルしました。")
                return
            candidate = dialog.selected_candidate
        target, _ = self.validator.validate(candidate)
        if target is None:
            self._finish("起動対象の確認に失敗しました。")
            return
        proposal = ApplicationLaunchProposalFactory().create(secrets.token_urlsafe(12), candidate, target)
        _, session = self.gate.prepare(APPLICATION_LAUNCH_CONTRACT, proposal)
        if session is None:
            self._finish("起動対象の確認に失敗しました。")
            return
        dialog = ActionConfirmationDialog(proposal, session, parent=self.parent())
        dialog.exec()
        result = self.gate.decide(APPLICATION_LAUNCH_CONTRACT, proposal, dialog.response)
        if result.grant is None:
            self._finish("アプリの起動をキャンセルしました。")
            return
        self.launch_token = CancellationToken()
        thread = self.launch_thread = QThread(self)
        worker = self.launch_worker = ApplicationLaunchWorker(self.executor, result.grant.grant_id,
            proposal, target, self.launch_token)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._launched)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_launch_thread(thread))
        thread.start()

    def _launched(self, outcome):
        messages = {
            ApplicationLaunchStatus.STARTED: "アプリを起動しました。",
            ApplicationLaunchStatus.HANDED_OFF: "アプリの起動をキャンセルしました。",
            ApplicationLaunchStatus.CANCELLED: "アプリの起動をキャンセルしました。",
        }
        self._finish(messages.get(outcome.status, "起動対象の確認に失敗しました。"))
