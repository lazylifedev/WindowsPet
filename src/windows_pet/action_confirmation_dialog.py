from datetime import datetime, timezone

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QPlainTextEdit

from .action_models import ActionProposal, ConfirmationDecision, ConfirmationResponse
from .confirmation_gate import ConfirmationSession


class ActionConfirmationDialog(QDialog):
    """Displays a proposal and returns a response; it never issues a grant."""
    def __init__(self, proposal: ActionProposal, session: ConfirmationSession, *, now=None, parent=None):
        super().__init__(parent); self.proposal = proposal; self.session = session
        self.now = now or (lambda: datetime.now(timezone.utc)); self._decided = False
        self.response = ConfirmationResponse(ConfirmationDecision.CLOSED, session.session_id, proposal.proposal_id, proposal.fingerprint)
        self.setWindowTitle("操作の確認"); self.status = QLabel()
        self.approve_button = QPushButton(proposal.preview.button_label); self.cancel_button = QPushButton("キャンセル")
        self.approve_button.setAutoDefault(False); self.approve_button.setDefault(False); self.cancel_button.setDefault(True)
        self.approve_button.clicked.connect(self.approve); self.cancel_button.clicked.connect(lambda: self._finish(ConfirmationDecision.CANCEL))
        detail = f"操作: {proposal.preview.operation}\n対象: {proposal.target.display_name}\n影響: {proposal.preview.impact}"
        if proposal.target.kind == "local_application": detail = f"アプリ名: {proposal.target.display_name}\n実行ファイル: {proposal.target.identifier}\n操作: アプリ起動\n影響: {proposal.preview.impact}\n管理者権限: 不要\nコマンドライン引数: なし\n起動後にWindowsPetから自動終了しません"
        layout = QVBoxLayout(self); layout.addWidget(QLabel(detail))
        if proposal.confirmation_type.value == "script_review":
            p = proposal.preview
            layout.addWidget(QLabel(f"目的: {p.purpose}\n対象: {p.target}\nSHA-256: {p.script_sha256_short}\n実行環境: {p.backend}\n作業ディレクトリ: {p.working_directory_display}\n環境変数: {p.environment_summary}\n想定される変更: {p.expected_changes}\n管理者権限: {p.requires_admin_display}\nタイムアウト: {p.timeout_display}\n実行後の確認: {p.verification_plan}\n元に戻す方法: {p.rollback_plan or 'なし'}"))
            script = QPlainTextEdit(p.script_text); script.setReadOnly(True); script.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); script.setMinimumHeight(180); layout.addWidget(script)
        layout.addWidget(self.status)
        buttons = QHBoxLayout(); buttons.addWidget(self.cancel_button); buttons.addWidget(self.approve_button); layout.addLayout(buttons)
        self.cancel_button.setFocus(); self.timer = QTimer(self); self.timer.timeout.connect(self._check_expiry); self.timer.start(250); self._check_expiry()

    def _check_expiry(self):
        expired = self.now() >= min(self.session.expires_at, self.proposal.expires_at); self.approve_button.setEnabled(not expired and not self._decided)
        if expired:
            self.status.setText("期限切れ")
            if not self._decided: self._finish(ConfirmationDecision.EXPIRED)
    def _finish(self, decision):
        if self._decided: return
        self._decided = True; self.response = ConfirmationResponse(decision, self.session.session_id, self.proposal.proposal_id, self.proposal.fingerprint); self.timer.stop()
        self.accept() if decision is ConfirmationDecision.APPROVE else self.reject()
    def approve(self): self._finish(ConfirmationDecision.APPROVE if self.now() < min(self.session.expires_at, self.proposal.expires_at) else ConfirmationDecision.EXPIRED)
    def reject_confirmation(self): self._finish(ConfirmationDecision.CANCEL)
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter): event.ignore(); return
        if event.key() == Qt.Key.Key_Escape: self._finish(ConfirmationDecision.CANCEL); return
        super().keyPressEvent(event)
    def closeEvent(self, event):
        if not self._decided: self.response = ConfirmationResponse(ConfirmationDecision.CLOSED, self.session.session_id, self.proposal.proposal_id, self.proposal.fingerprint)
        super().closeEvent(event)
