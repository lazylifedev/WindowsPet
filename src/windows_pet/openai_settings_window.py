from __future__ import annotations

from PySide6.QtCore import QThread, Signal, QObject, Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QLabel
from openai import OpenAI

from .ai_client import classify_openai_error, model_name
from .openai_credentials import (delete_api_key, get_api_key, has_environment_key,
                                  has_stored_key, save_api_key)


class _CheckWorker(QObject):
    finished = Signal(bool, str)
    def __init__(self, key): super().__init__(); self.key = key
    @Slot()
    def run(self):
        try:
            OpenAI(api_key=self.key, timeout=15, max_retries=0).responses.create(
                model=model_name(), input="Reply with OK.", store=False)
            self.finished.emit(True, "OpenAI API に接続できました。")
        except Exception as exc:
            self.finished.emit(False, classify_openai_error(exc).args[0])


class OpenAISettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenAI API 設定"); self.setMinimumWidth(430)
        self._check_thread = None; self._check_worker = None; self._check_in_progress = False
        self._check_generation = 0; self._last_check_result = None
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.Password); self.key.setPlaceholderText("sk-…")
        self.show_button = QPushButton("表示"); self.show_button.setCheckable(True)
        self.show_button.toggled.connect(lambda v: self.key.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        row = QVBoxLayout(); row.addWidget(self.key); row.addWidget(self.show_button)
        self.status = QLabel(); self.check = QPushButton("接続確認"); self.save = QPushButton("保存"); self.remove = QPushButton("保存済みAPIキーを削除")
        self.check.clicked.connect(self.check_connection); self.save.clicked.connect(self.save_key); self.remove.clicked.connect(self.remove_key)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject)
        layout = QFormLayout(self); layout.addRow("APIキー", row); layout.addRow(self.status); layout.addRow(self.check, self.save); layout.addRow(self.remove, buttons)
        self._refresh()

    def _refresh(self):
        self.key.clear(); self.show_button.setChecked(False)
        if has_environment_key(): self.status.setText("環境変数 OPENAI_API_KEY を使用しています。保存済みのAPIキーより優先されます。")
        elif has_stored_key(): self.status.setText("Windows Credential Manager にAPIキーが設定されています。")
        else: self.status.setText("APIキーは未設定です。")
        self._update_controls()

    def _update_controls(self):
        checking = self._check_in_progress
        environment = has_environment_key()
        self.key.setEnabled(not checking and not environment)
        self.show_button.setEnabled(not checking and not environment)
        self.check.setEnabled(not checking and bool(get_api_key() or self.key.text().strip()))
        self.save.setEnabled(not checking and not environment)
        self.remove.setEnabled(not checking and has_stored_key())

    def showEvent(self, event):
        self._update_controls()
        if self._check_in_progress: self.status.setText("確認中…")
        elif self._last_check_result is not None: self.status.setText(self._last_check_result[1])
        super().showEvent(event)

    def save_key(self):
        value = self.key.text().strip()
        if not value: QMessageBox.warning(self, "OpenAI API 設定", "APIキーを入力してください。"); return
        try: save_api_key(value)
        except Exception: QMessageBox.warning(self, "OpenAI API 設定", "APIキーをWindows Credential Managerへ保存できませんでした。"); return
        self._refresh(); self.status.setText("APIキーを保存しました。")

    def remove_key(self):
        delete_api_key(); self._refresh()
        self.status.setText("保存済みAPIキーを削除しました。" + (" 環境変数 OPENAI_API_KEY は引き続き使用されます。" if has_environment_key() else ""))

    def check_connection(self):
        if self._check_in_progress: return False
        key = get_api_key()
        if not key: self.status.setText("APIキーを設定してください。"); return False
        self._check_generation += 1; generation = self._check_generation; self._check_in_progress = True
        self.status.setText("確認中…"); self._update_controls()
        self._check_thread = QThread(self); self._check_worker = _CheckWorker(key); self._check_worker.moveToThread(self._check_thread)
        self._check_thread.started.connect(self._check_worker.run)
        self._check_worker.finished.connect(lambda ok, text: self._checked(generation, ok, text))
        self._check_worker.finished.connect(self._check_thread.quit)
        self._check_thread.finished.connect(self._check_thread_done)
        self._check_thread.start(); return True

    def _checked(self, generation, ok, text):
        if generation != self._check_generation: return
        self._last_check_result = (ok, text)
        if self.isVisible(): self.status.setText(text)

    def _check_thread_done(self):
        thread = self._check_thread
        self._check_thread = None; self._check_worker = None; self._check_in_progress = False
        if thread is not None: thread.deleteLater()
        if self.isVisible() and self._last_check_result is not None: self.status.setText(self._last_check_result[1])
        self._update_controls()

    def shutdown(self):
        thread = self._check_thread
        if thread is not None and thread.isRunning(): thread.quit(); thread.wait(16000)
        if thread is not None and not thread.isRunning(): self._check_thread_done()

    def closeEvent(self, event):
        self.show_button.setChecked(False)
        super().closeEvent(event)
