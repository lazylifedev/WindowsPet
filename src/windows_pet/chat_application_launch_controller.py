from __future__ import annotations

import secrets
from PySide6.QtCore import QObject, QThread
from .action_confirmation_dialog import ActionConfirmationDialog
from .application_candidate_resolver import ApplicationCandidateResolver, ApplicationCandidateResolverWorker, CandidateResolutionStatus
from .application_candidate_selection_dialog import ApplicationCandidateSelectionDialog
from .application_launch import ApplicationLaunchExecutor, ApplicationLaunchProposalFactory, ApplicationLaunchStatus, ApplicationLaunchValidator, ApplicationLaunchWorker, APPLICATION_LAUNCH_CONTRACT
from .audit_log import NullAuditSink
from .cancellation import CancellationToken
from .confirmation_gate import ConfirmationGate, ConfirmationResultCode


class ChatApplicationLaunchController(QObject):
    def __init__(self, complete, parent=None, audit=None, resolver=None, validator=None, show_status=None,
                 resolver_worker_factory=ApplicationCandidateResolverWorker,
                 selection_dialog_factory=ApplicationCandidateSelectionDialog,
                 confirmation_dialog_factory=ActionConfirmationDialog,
                 proposal_factory=ApplicationLaunchProposalFactory,
                 confirmation_gate=None, executor=None,
                 launch_worker_factory=ApplicationLaunchWorker, thread_factory=QThread,
                 token_factory=CancellationToken):
        super().__init__(parent)
        self.complete, self.show_status = complete, show_status or (lambda _: None)
        self.audit = audit or NullAuditSink(); self.validator = validator or ApplicationLaunchValidator()
        self.resolver = resolver or ApplicationCandidateResolver(validator=self.validator)
        self.resolver_worker_factory, self.selection_dialog_factory = resolver_worker_factory, selection_dialog_factory
        self.confirmation_dialog_factory, self.proposal_factory = confirmation_dialog_factory, proposal_factory
        self.gate = confirmation_gate or ConfirmationGate(audit=self.audit)
        self.executor = executor or ApplicationLaunchExecutor(self.gate.grants, validator=self.validator, audit=self.audit)
        self.launch_worker_factory, self.thread_factory, self.token_factory = launch_worker_factory, thread_factory, token_factory
        self.resolver_thread = self.resolver_worker = self.resolver_token = None
        self.launch_thread = self.launch_worker = self.launch_token = None
        self._grant_id = None
        self._busy = False

    @property
    def is_busy(self): return self._busy

    def request(self, request):
        if self._busy: return False
        self._busy = True; self.resolver_token = self.token_factory(); self.show_status("アプリの起動確認を準備しています。")
        thread = self.resolver_thread = self.thread_factory(self)
        worker = self.resolver_worker = self.resolver_worker_factory(self.resolver, request, self.resolver_token)
        worker.moveToThread(thread); thread.started.connect(worker.run); worker.finished.connect(self._resolved); worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater); thread.finished.connect(lambda: self._cleanup_resolver_thread(thread)); thread.finished.connect(thread.deleteLater); thread.start(); return True

    def cancel(self):
        if self.resolver_token is not None: self.resolver_token.cancel()
        if self.launch_token is not None: self.launch_token.cancel()
        if self._grant_id:
            self.gate.grants.cancel(self._grant_id)

    def shutdown(self):
        self.cancel()
        for thread in (self.resolver_thread, self.launch_thread):
            if thread is not None and thread.isRunning(): thread.quit(); thread.wait(7000)
        self._busy = False

    def _cleanup_resolver_thread(self, thread):
        if self.resolver_thread is thread: self.resolver_thread = self.resolver_worker = self.resolver_token = None
    def _cleanup_launch_thread(self, thread):
        if self.launch_thread is thread: self.launch_thread = self.launch_worker = self.launch_token = None
    def _finish(self, text):
        if self._busy:
            self._busy = False
            self._grant_id = None
            self.complete(text)

    def _resolved(self, outcome):
        if outcome.status is CandidateResolutionStatus.CANCELLED: self._finish("アプリの起動をキャンセルしました。"); return
        if outcome.status is CandidateResolutionStatus.NOT_FOUND or (outcome.status is CandidateResolutionStatus.SUCCESS and not outcome.candidates): self._finish("アプリを見つけられませんでした。実行ファイルのフルパスを入力してください。"); return
        if outcome.status is not CandidateResolutionStatus.SUCCESS: self._finish("アプリの起動候補を確認できませんでした。"); return
        candidate = outcome.candidates[0]
        if len(outcome.candidates) > 1:
            dialog = self.selection_dialog_factory(outcome.candidates, parent=self.parent())
            if dialog.exec() != dialog.Accepted or dialog.selected_candidate is None: self._finish("アプリの起動をキャンセルしました。"); return
            candidate = dialog.selected_candidate
        target, _ = self.validator.validate(candidate)
        if target is None: self._finish("起動条件が変わったため、もう一度依頼してください。"); return
        proposal = self.proposal_factory().create(secrets.token_urlsafe(12), candidate, target)
        _, session = self.gate.prepare(APPLICATION_LAUNCH_CONTRACT, proposal)
        if session is None: self._finish("このアプリは起動できません。"); return
        dialog = self.confirmation_dialog_factory(proposal, session, parent=self.parent()); dialog.exec()
        result = self.gate.decide(APPLICATION_LAUNCH_CONTRACT, proposal, dialog.response)
        if result.grant is None:
            text = "確認の有効期限が切れました。もう一度依頼してください。" if result.reason is ConfirmationResultCode.EXPIRED else ("このアプリは起動できません。" if result.reason in (ConfirmationResultCode.POLICY_DENIED, ConfirmationResultCode.SESSION_NOT_FOUND, ConfirmationResultCode.SESSION_NOT_PENDING, ConfirmationResultCode.PROPOSAL_MISMATCH, ConfirmationResultCode.FINGERPRINT_MISMATCH) else "アプリを起動しませんでした。")
            self._finish(text); return
        self._grant_id = result.grant.grant_id
        self.launch_token = self.token_factory(); thread = self.launch_thread = self.thread_factory(self)
        worker = self.launch_worker = self.launch_worker_factory(self.executor, result.grant.grant_id, proposal, target, self.launch_token)
        worker.moveToThread(thread); thread.started.connect(worker.run); worker.finished.connect(self._launched); worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater); thread.finished.connect(lambda: self._cleanup_launch_thread(thread)); thread.finished.connect(thread.deleteLater); thread.start()

    def _launched(self, outcome):
        messages = {ApplicationLaunchStatus.STARTED: "アプリを起動しました。", ApplicationLaunchStatus.HANDED_OFF: "アプリへ起動要求を渡しました。", ApplicationLaunchStatus.CANCELLED: "アプリの起動をキャンセルしました。", ApplicationLaunchStatus.REJECTED: "このアプリは起動できません。", ApplicationLaunchStatus.FAILED: "アプリを起動できませんでした。"}
        if outcome.status is ApplicationLaunchStatus.REJECTED and outcome.result_code == "expired":
            self._finish("確認の有効期限が切れました。もう一度依頼してください。")
            return
        self._finish(messages.get(outcome.status, "アプリを起動できませんでした。"))
