from __future__ import annotations

import secrets
from PySide6.QtCore import QObject, QThread

from .action_confirmation_dialog import ActionConfirmationDialog
from .application_candidate_resolver import ApplicationCandidateResolver, ApplicationCandidateResolverWorker, CandidateResolutionStatus
from .application_candidate_selection_dialog import ApplicationCandidateSelectionDialog
from .application_launch import (ApplicationLaunchExecutor, ApplicationLaunchProposalFactory, ApplicationLaunchStatus,
    ApplicationLaunchValidator, ApplicationLaunchWorker, APPLICATION_LAUNCH_CONTRACT)
from .audit_log import NullAuditSink
from .cancellation import CancellationToken
from .confirmation_gate import ConfirmationGate


class ChatApplicationLaunchController(QObject):
    def __init__(self, complete, parent=None, audit=None, resolver=None, validator=None):
        super().__init__(parent); self.complete = complete; self.audit = audit or NullAuditSink(); self.validator = validator or ApplicationLaunchValidator(); self.resolver = resolver or ApplicationCandidateResolver(validator=self.validator)
        self.gate = ConfirmationGate(audit=self.audit); self.executor = ApplicationLaunchExecutor(self.gate.grants, validator=self.validator, audit=self.audit); self._thread = None; self._worker = None; self._token = None; self._busy = False
    def request(self, request):
        if self._busy: self.complete("現在、別のアプリ起動処理を確認中です。"); return
        self._busy = True; self._token = CancellationToken(); self.complete("アプリの起動候補を確認しています。")
        self._thread = QThread(self); self._worker = ApplicationCandidateResolverWorker(self.resolver, request, self._token); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run); self._worker.finished.connect(self._resolved); self._worker.finished.connect(self._thread.quit); self._thread.finished.connect(self._cleanup); self._thread.start()
    def cancel(self):
        if self._token: self._token.cancel()
    def shutdown(self):
        self.cancel()
        if self._thread and self._thread.isRunning(): self._thread.quit(); self._thread.wait(2000)
    def _cleanup(self): self._thread = self._worker = self._token = None
    def _finish(self, text): self._busy = False; self.complete(text)
    def _resolved(self, outcome):
        if outcome.status is CandidateResolutionStatus.CANCELLED: self._finish("アプリの起動をキャンセルしました。"); return
        if outcome.status is not CandidateResolutionStatus.SUCCESS or not outcome.candidates: self._finish("アプリを見つけられませんでした。実行ファイルのフルパスを入力してください。"); return
        candidate = outcome.candidates[0]
        if len(outcome.candidates) > 1:
            dialog = ApplicationCandidateSelectionDialog(outcome.candidates, self.parent())
            if dialog.exec() != dialog.Accepted or dialog.selected_candidate is None: self._finish("アプリの起動をキャンセルしました。"); return
            candidate = dialog.selected_candidate
        target, code = self.validator.validate(candidate)
        if not target: self._finish("起動条件が変わったため、もう一度依頼してください。"); return
        proposal = ApplicationLaunchProposalFactory().create(secrets.token_urlsafe(12), candidate, target)
        _, session = self.gate.prepare(APPLICATION_LAUNCH_CONTRACT, proposal)
        if session is None: self._finish("起動条件が変わったため、もう一度依頼してください。"); return
        dialog = ActionConfirmationDialog(proposal, session, self.parent())
        dialog.exec(); result = self.gate.decide(APPLICATION_LAUNCH_CONTRACT, proposal, dialog.response)
        if result.grant is None: self._finish("アプリを起動しませんでした。"); return
        worker = ApplicationLaunchWorker(self.executor, result.grant.grant_id, proposal, target, self._token)
        thread = QThread(self); worker.moveToThread(thread); self._thread, self._worker = thread, worker
        thread.started.connect(worker.run); worker.finished.connect(self._launched); worker.finished.connect(thread.quit); thread.finished.connect(self._cleanup); thread.start()
    def _launched(self, outcome):
        self._busy = False
        self.complete("アプリを起動しました。" if outcome.status is ApplicationLaunchStatus.STARTED else "アプリへ起動要求を渡しました。" if outcome.status is ApplicationLaunchStatus.HANDED_OFF else "アプリを起動できませんでした。" if outcome.status is ApplicationLaunchStatus.FAILED else "起動条件が変わったため、もう一度依頼してください。")
