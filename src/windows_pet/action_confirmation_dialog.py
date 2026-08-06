from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout

from .action_models import ActionProposal, ConfirmationDecision, ConfirmationResponse
from .confirmation_gate import ConfirmationSession




class ActionConfirmationDialog(QDialog):
    """Session-bound display only; it never issues grants or performs actions."""

    def __init__(self, proposal: ActionProposal, session: ConfirmationSession,
                 now=None, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self.session = session
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.response = ConfirmationResponse(ConfirmationDecision.CLOSED, session.session_id, proposal.proposal_id, proposal.fingerprint)
        self._decided = False
        self.setWindowTitle("操作の確認")
        self.status = QLabel()
        self.approve_button = QPushButton(proposal.preview.button_label)
        self.cancel_button = QPushButton("キャンセル")
        self.approve_button.setDefault(False)
        self.approve_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.approve_cancel)
        self.approve_button.clicked.connect(self.approve)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"操作: {proposal.preview.operation}\n対象: {proposal.target.display_name}\n影響: {proposal.preview.impact}"))
        layout.addWidget(self.status)
        buttons = QHBoxLayout(); buttons.addWidget(self.cancel_button); buttons.addWidget(self.approve_button); layout.addLayout(buttons)
        self.cancel_button.setFocus()
        self.timer = QTimer(self); self.timer.timeout.connect(self._check_expiry); self.timer.start(250)
        self._check_expiry()

    def _check_expiry(self):
        expired = self.now() >= min(self.session.expires_at, self.proposal.expires_at)
        self.approve_button.setEnabled(not expired and not self._decided)
        if expired:
            self.status.setText("期限切れ")

    def _finish(self, decision):
        if self._decided: return
        self._decided = True
        self.response = ConfirmationResponse(decision, self.session.session_id, self.proposal.proposal_id, self.proposal.fingerprint)
        self.timer.stop(); self.accept() if decision is ConfirmationDecision.APPROVE else self.reject()

    def approve(self):
        if self.now() < min(self.session.expires_at, self.proposal.expires_at): self._finish(ConfirmationDecision.APPROVE)
        else: self._finish(ConfirmationDecision.EXPIRED)

    def approve_cancel(self): self._finish(ConfirmationDecision.CANCEL)
    def reject_confirmation(self): self._finish(ConfirmationDecision.CANCEL)
    def closeEvent(self, event):
        if not self._decided: self.response = ConfirmationResponse(ConfirmationDecision.CLOSED, self.session.session_id, self.proposal.proposal_id, self.proposal.fingerprint)
        super().closeEvent(event)
