from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout

from .memory.models import MemoryRecord


class MemoryWindow(QDialog):
    """Small local inspection/deletion surface; it never exposes raw DB access."""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Personal Memory")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Personal Memory（このPCだけに保存）"))
        filters = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(["すべて", "preference", "fact", "workflow", "resource", "communication"])
        self.category.currentTextChanged.connect(self.refresh)
        filters.addWidget(QLabel("category")); filters.addWidget(self.category); filters.addStretch()
        self.delete_button = QPushButton("選択した記憶を削除")
        self.delete_button.clicked.connect(self.delete_selected)
        filters.addWidget(self.delete_button); layout.addLayout(filters)
        self.list_widget = QListWidget(); self.list_widget.setSelectionMode(QListWidget.SingleSelection); layout.addWidget(self.list_widget)
        self.refresh()

    def _records(self) -> list[MemoryRecord]:
        category = self.category.currentText()
        return self.service.list(category=None if category == "すべて" else category)

    def refresh(self) -> None:
        self.list_widget.clear()
        for record in self._records():
            protected = " protected" if record.protected else ""
            item = QListWidgetItem(f"[{record.category}/{record.kind.value}{protected}] {record.key} = {record.display_value}")
            item.setData(Qt.UserRole, record.memory_id)
            self.list_widget.addItem(item)
        self.delete_button.setEnabled(self.list_widget.count() > 0)

    def delete_selected(self) -> bool:
        item = self.list_widget.currentItem()
        if item is None:
            return False
        if not self.service.forget(item.data(Qt.UserRole)):
            QMessageBox.warning(self, "削除できません", "記憶を削除できませんでした。")
            return False
        self.refresh()
        return True
