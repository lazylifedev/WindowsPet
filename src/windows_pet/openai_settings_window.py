from __future__ import annotations
from PySide6.QtCore import QThread, Signal, QObject, Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QLabel
from openai import OpenAI
from .openai_credentials import delete_api_key, get_api_key, has_environment_key, save_api_key
from .ai_client import classify_openai_error

class _CheckWorker(QObject):
    finished = Signal(bool, str)
    def __init__(self, key): super().__init__(); self.key = key
    @Slot()
    def run(self):
        try:
            OpenAI(api_key=self.key, timeout=15, max_retries=0).responses.create(model="gpt-5-mini", input="Reply with OK.", store=False)
            self.finished.emit(True, "OpenAI API に接続できました。")
        except Exception as exc: self.finished.emit(False, classify_openai_error(exc).args[0])

class OpenAISettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("OpenAI API 設定"); self.setMinimumWidth(430); self._closed_generation = 0
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.Password); self.key.setPlaceholderText("sk-…")
        self.show_button = QPushButton("表示"); self.show_button.setCheckable(True); self.show_button.toggled.connect(lambda v: self.key.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        row = QVBoxLayout(); row.addWidget(self.key); row.addWidget(self.show_button)
        self.status = QLabel(); self.check = QPushButton("接続確認"); self.save = QPushButton("保存"); self.remove = QPushButton("API キーを削除")
        self.check.clicked.connect(self.check_connection); self.save.clicked.connect(self.save_key); self.remove.clicked.connect(self.remove_key)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel); buttons.rejected.connect(self.reject)
        layout = QFormLayout(self); layout.addRow("API キー", row); layout.addRow(self.status); layout.addRow(self.check, self.save); layout.addRow(self.remove, buttons); self._refresh()
    def _refresh(self):
        self.key.clear(); self.status.setText("環境変数 OPENAI_API_KEY を使用しています。" if has_environment_key() else ("設定済み" if get_api_key() else "未設定"))
    def save_key(self):
        value = self.key.text().strip()
        if not value: QMessageBox.warning(self, "OpenAI API 設定", "API キーを入力してください。"); return
        try: save_api_key(value)
        except Exception as exc: QMessageBox.warning(self, "OpenAI API 設定", str(exc)); return
        self._refresh(); self.accept()
    def remove_key(self): delete_api_key(); self._refresh(); self.status.setText("API キーを削除しました。")
    def check_connection(self):
        key = self.key.text().strip() or get_api_key()
        if not key: self.status.setText("API キーを設定してください。"); return
        self.check.setEnabled(False); self.status.setText("確認中…"); generation = self._closed_generation; self.thread = QThread(self); self.worker = _CheckWorker(key); self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run); self.worker.finished.connect(lambda ok, text: self._checked(generation, ok, text)); self.worker.finished.connect(self.worker.deleteLater); self.worker.finished.connect(self.thread.quit); self.thread.finished.connect(self.thread.deleteLater); self.thread.start()
    def _checked(self, generation, ok, text):
        if generation != self._closed_generation or not self.isVisible(): return
        self.check.setEnabled(True); self.status.setText(text)
    def closeEvent(self, event): self._closed_generation += 1; super().closeEvent(event)
