from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from .action_confirmation_dialog import ActionConfirmationDialog
from .action_models import ConfirmationDecision
from .application_launch import (APPLICATION_LAUNCH_CONTRACT, ApplicationLaunchExecutor, ApplicationLaunchProposalFactory,
                                  ApplicationLaunchStatus, ApplicationLaunchValidator, ApplicationLaunchWorker)
from .cancellation import CancellationToken
from .confirmation_gate import ConfirmationGate
from .local_inspection_service import LocalInspectionService
from .local_inspection_worker import LocalInspectionWorker


class LocalInspectionWindow(QDialog):
    """Read-only inspection UI with an explicit confirmation boundary for launching."""
    def __init__(self, parent=None, service_factory=LocalInspectionService, audit_sink=None,
                 confirmation_gate_factory=ConfirmationGate, proposal_factory=ApplicationLaunchProposalFactory,
                 validator_factory=ApplicationLaunchValidator, executor_factory=ApplicationLaunchExecutor,
                 dialog_factory=ActionConfirmationDialog, launch_worker_factory=ApplicationLaunchWorker):
        super().__init__(parent); self.setWindowTitle("PC調査"); self.resize(620, 520)
        self.service_factory = service_factory; self.audit_sink = audit_sink
        self.confirmation_gate_factory = confirmation_gate_factory; self.proposal_factory = proposal_factory
        self.validator_factory = validator_factory; self.executor_factory = executor_factory
        self.dialog_factory = dialog_factory; self.launch_worker_factory = launch_worker_factory
        self.thread = self.worker = self.token = None
        self.launch_thread = self.launch_worker = self.launch_token = None
        self.snapshot = None; self._launch_busy = False
        self.status = QLabel("未調査"); self.summary = QLabel(); self.selected_detail = QLabel("アプリを選択してください")
        self.launch_status = QLabel(); self.query = QLineEdit(); self.query.setPlaceholderText("アプリ名を検索")
        self.results = QListWidget(); self.refresh = QPushButton("更新"); self.launch_button = QPushButton("選択したアプリを起動"); self.launch_button.setEnabled(False)
        self.refresh.clicked.connect(self.start_inspection); self.query.textChanged.connect(self.search)
        self.results.itemSelectionChanged.connect(self._selection_changed); self.launch_button.clicked.connect(self.start_selected_application)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("PCの状態を読み取り専用で調査します。アプリ起動は確認後に実行します。"))
        for widget in (self.status, self.summary, self.query, self.results, self.selected_detail, self.launch_status, self.launch_button, self.refresh): layout.addWidget(widget)

    def showEvent(self, event):
        super().showEvent(event)
        if self.snapshot is None and self.thread is None: self.start_inspection()

    def start_inspection(self):
        if self.thread is not None and self.thread.isRunning(): return
        self.status.setText("調査中…"); self.refresh.setEnabled(False); self.token = CancellationToken(); self.thread = QThread(self); self.worker = LocalInspectionWorker(self.service_factory(), self.token); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run); self.worker.finished.connect(self._finished); self.worker.finished.connect(self.thread.quit); self.thread.finished.connect(self._thread_finished); self.thread.start()

    def _completed(self, snapshot):
        self.snapshot = snapshot; system = snapshot.system
        self.status.setText("調査完了")
        self.summary.setText(f"OS: {system.os_name} {system.version} build {system.build}\nコンピューター: {system.computer_name} / ユーザー: {system.username}\nPATH: {snapshot.path.item_count}件 / App Paths: {len(snapshot.app_paths)}件 / インストール済み: {len(snapshot.installed_apps)}件")
        self.search()

    def _thread_finished(self):
        if self.worker is not None: self.worker.deleteLater()
        if self.thread is not None: self.thread.deleteLater()
        self.worker = self.thread = None; self.refresh.setEnabled(True); self._selection_changed()

    def _finished(self, outcome):
        if outcome.snapshot is not None: self._completed(outcome.snapshot)
        elif outcome.status.value == "cancelled": self.status.setText("調査をキャンセルしました")
        else: self.status.setText("調査に失敗しました")

    def search(self):
        self.results.clear()
        if self.snapshot is None: return
        for candidate in self.service_factory().search(self.snapshot, self.query.text()):
            item = QListWidgetItem(f"{candidate.display_name} | {candidate.version} | {candidate.publisher} | {candidate.source}")
            item.setData(Qt.ItemDataRole.UserRole, candidate); self.results.addItem(item)
        self._selection_changed()

    def _selection_changed(self):
        item = self.results.currentItem(); candidate = item.data(Qt.ItemDataRole.UserRole) if item else None
        valid = candidate is not None and candidate.executable_exists is True and str(candidate.executable_path).casefold().endswith(".exe")
        if candidate: self.selected_detail.setText(f"{candidate.display_name}\n{candidate.executable_path}")
        else: self.selected_detail.setText("アプリを選択してください")
        self.launch_button.setEnabled(bool(valid and not self._launch_busy and self.launch_thread is None))

    def start_selected_application(self):
        if self._launch_busy: return
        item = self.results.currentItem(); candidate = item.data(Qt.ItemDataRole.UserRole) if item else None
        target, code = self.validator_factory().validate(candidate) if candidate else (None, None)
        if target is None: self.launch_status.setText(f"起動できません: {getattr(code, 'value', '未選択')}"); return
        proposal = self.proposal_factory().create("local-inspection-launch", candidate, target)
        gate = self.confirmation_gate_factory(audit=self.audit_sink) if self.audit_sink is not None else self.confirmation_gate_factory()
        decision, session = gate.prepare(APPLICATION_LAUNCH_CONTRACT, proposal)
        if session is None: self.launch_status.setText("起動がポリシーで拒否されました"); return
        dialog = self.dialog_factory(proposal, session, parent=self); dialog.exec(); result = gate.decide(APPLICATION_LAUNCH_CONTRACT, proposal, dialog.response)
        if not result.success or dialog.response.decision is not ConfirmationDecision.APPROVE: self.launch_status.setText(f"起動しません: {result.reason.value}"); return
        self._launch_busy = True; self.launch_token = CancellationToken(); self.launch_thread = QThread(self)
        executor = self.executor_factory(gate.grants, audit=self.audit_sink) if self.audit_sink is not None else self.executor_factory(gate.grants)
        self.launch_worker = self.launch_worker_factory(executor, result.grant.grant_id, proposal, target, self.launch_token); self.launch_worker.moveToThread(self.launch_thread)
        self.launch_thread.started.connect(self.launch_worker.run); self.launch_worker.finished.connect(self._launch_finished); self.launch_worker.finished.connect(self.launch_thread.quit); self.launch_thread.finished.connect(self._launch_thread_finished); self.launch_thread.start(); self._selection_changed()

    def _launch_finished(self, outcome): self.launch_status.setText({ApplicationLaunchStatus.STARTED: "起動を開始しました", ApplicationLaunchStatus.HANDED_OFF: "起動処理を引き渡しました", ApplicationLaunchStatus.CANCELLED: "起動をキャンセルしました"}.get(outcome.status, "起動に失敗しました"))
    def _launch_thread_finished(self):
        if self.launch_worker is not None: self.launch_worker.deleteLater()
        if self.launch_thread is not None: self.launch_thread.deleteLater()
        self.launch_worker = self.launch_thread = None; self._launch_busy = False; self._selection_changed()
    def request_cancel(self):
        if self.token is not None: self.token.cancel()
    def shutdown(self, timeout_ms=7000):
        self.request_cancel()
        if self.launch_token is not None: self.launch_token.cancel()
        ok = True
        if self.thread is not None and self.thread.isRunning(): self.thread.quit(); ok = self.thread.wait(timeout_ms)
        if self.launch_thread is not None and self.launch_thread.isRunning(): self.launch_thread.quit(); ok = self.launch_thread.wait(timeout_ms) and ok
        return ok
    def closeEvent(self, event): self.shutdown(); super().closeEvent(event)
