from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QVBoxLayout


class ApplicationCandidateSelectionDialog(QDialog):
    def __init__(self, candidates, parent=None):
        super().__init__(parent); self.candidates = tuple(candidates); self.selected_candidate = None
        self.setWindowTitle("起動候補の選択"); layout = QVBoxLayout(self)
        self.table = QTableWidget(len(self.candidates), 5, self); self.table.setHorizontalHeaderLabels(["アプリ名", "version", "publisher", "source", "実行ファイル"])
        for row, candidate in enumerate(self.candidates):
            for column, value in enumerate((candidate.display_name, candidate.version, candidate.publisher, candidate.source, candidate.executable_path)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.clearSelection(); layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self); self.confirm_button = self.buttons.addButton("この候補を確認", QDialogButtonBox.AcceptRole); self.confirm_button.setEnabled(False)
        self.buttons.rejected.connect(self.reject); self.confirm_button.clicked.connect(self._confirm); self.table.itemSelectionChanged.connect(lambda: self.confirm_button.setEnabled(bool(self.table.selectedItems()))); layout.addWidget(self.buttons)
    def _confirm(self):
        row = self.table.currentRow()
        if row >= 0: self.selected_candidate = self.candidates[row]; self.accept()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter): event.ignore(); return
        super().keyPressEvent(event)
