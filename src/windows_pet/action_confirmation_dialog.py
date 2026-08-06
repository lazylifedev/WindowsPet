from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from .action_models import ActionProposal, ConfirmationDecision


class ActionConfirmationDialog(QDialog):
    """Displays a proposal; it never executes actions or issues grants."""
    def __init__(self, proposal: ActionProposal, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self.decision = ConfirmationDecision.CLOSED
        self.setWindowTitle("操作の確認")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"操作: {proposal.preview.operation}\n対象: {proposal.target.display_name}\n影響: {proposal.preview.impact}"))
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("キャンセル")
        self.approve_button = QPushButton(proposal.preview.button_label)
        self.approve_button.setAutoDefault(False)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject_confirmation)
        self.approve_button.clicked.connect(self.approve_confirmation)
        buttons.addWidget(self.cancel_button); buttons.addWidget(self.approve_button)
        layout.addLayout(buttons)
        self.cancel_button.setFocus()

    def approve_confirmation(self):
        self.decision = ConfirmationDecision.APPROVE
        self.accept()

    def reject_confirmation(self):
        self.decision = ConfirmationDecision.CANCEL
        self.reject()

    def closeEvent(self, event):
        self.decision = ConfirmationDecision.CLOSED
        super().closeEvent(event)
