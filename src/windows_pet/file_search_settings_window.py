from __future__ import annotations

import os
import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .file_search_models import SearchRoot
from .file_search_settings import SearchSettings


class _ConnectionCheck(QThread):
    finished = Signal(str, str)

    def __init__(self, root_id: str, path: str, parent=None):
        super().__init__(parent)
        self.root_id, self.path = root_id, path

    def run(self):
        try:
            with os.scandir(self.path) as entries:
                next(entries, None)
            state = "接続可能"
        except FileNotFoundError:
            state = "パスが見つかりません"
        except PermissionError:
            state = "アクセス権がありません"
        except NotADirectoryError:
            state = "フォルダーではありません"
        except OSError:
            state = "ネットワークへ接続できません"
        self.finished.emit(self.root_id, state)


class SearchRootDialog(QDialog):
    rootReady = Signal(object)

    def __init__(self, root: SearchRoot | None = None, parent=None):
        super().__init__(parent)
        self.root = root
        self.setWindowTitle("検索ルートを編集" if root else "検索ルートを追加")
        self.setMinimumWidth(560)
        self.alias = QLineEdit(root.alias if root else "")
        self.path = QLineEdit(root.path if root else "")
        self.path.setPlaceholderText("C:\\Users\\... または \\\\server\\share")
        browse = QPushButton("参照…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout(); path_row.addWidget(self.path); path_row.addWidget(browse)
        form = QFormLayout(); form.addRow("エイリアス *", self.alias); form.addRow("フォルダーパス *", path_row)
        self.error = QLabel(); self.error.setStyleSheet("color: #b3261e")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("キャンセル")
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(self.error); layout.addWidget(buttons)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(self, "フォルダーを選択", self.path.text().strip() or str(Path.home()))
        if chosen: self.path.setText(os.path.normpath(chosen))

    def _accept(self):
        alias, path = " ".join(self.alias.text().split()), os.path.normpath(self.path.text().strip())
        if not alias: self.error.setText("エイリアスを入力してください。"); return
        if not self.path.text().strip(): self.error.setText("フォルダーパスを入力または選択してください。"); return
        self.rootReady.emit(SearchRoot(self.root.id if self.root else uuid.uuid4().hex, alias, path, self.root.enabled if self.root else True))
        self.accept()


class FileSearchSettingsWindow(QDialog):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings or SearchSettings.load(); self._checks = {}
        self.setWindowTitle("ファイル検索設定"); self.resize(900, 520)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["有効", "エイリアス", "フォルダーパス", "接続状態"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive); self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda row, _: self.edit_root(row))
        self.table.itemChanged.connect(self._enabled_changed)
        add = QPushButton("追加"); edit = QPushButton("編集"); delete = QPushButton("削除"); check = QPushButton("接続確認")
        add.clicked.connect(self.add_root); edit.clicked.connect(self.edit_root); delete.clicked.connect(self.delete_root); check.clicked.connect(self.check_root)
        self.edit_button, self.delete_button, self.check_button = edit, delete, check
        self.table.itemSelectionChanged.connect(self._update_buttons)
        actions = QHBoxLayout(); actions.addWidget(add); actions.addWidget(edit); actions.addWidget(delete); actions.addStretch(); actions.addWidget(check)
        self.status = QLabel("変更は保存ボタンで反映されます。")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Save).setText("保存"); buttons.button(QDialogButtonBox.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.save_settings); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addWidget(self.table); layout.addLayout(actions); layout.addWidget(self.status); layout.addWidget(buttons)
        self.refresh(); self._update_buttons()

    def refresh(self):
        self.table.blockSignals(True); self.table.setRowCount(len(self.settings.roots))
        for i, root in enumerate(self.settings.roots):
            enabled = QTableWidgetItem(); enabled.setCheckState(Qt.Checked if root.enabled else Qt.Unchecked)
            self.table.setItem(i, 0, enabled); self.table.setItem(i, 1, QTableWidgetItem(root.alias)); self.table.setItem(i, 2, QTableWidgetItem(root.path)); self.table.setItem(i, 3, QTableWidgetItem("未確認"))
        self.table.blockSignals(False); self._update_buttons()

    def _update_buttons(self):
        has = self.table.currentRow() >= 0; self.edit_button.setEnabled(has); self.delete_button.setEnabled(has); self.check_button.setEnabled(has)

    def _enabled_changed(self, item):
        if item.column() == 0 and 0 <= item.row() < len(self.settings.roots):
            root = self.settings.roots[item.row()]; self.settings.roots[item.row()] = SearchRoot(root.id, root.alias, root.path, item.checkState() == Qt.Checked)

    def add_root(self):
        dialog = SearchRootDialog(parent=self); dialog.rootReady.connect(self._add); dialog.exec()

    def _add(self, root):
        if any(os.path.normcase(r.path) == os.path.normcase(root.path) for r in self.settings.roots): QMessageBox.warning(self, "ファイル検索設定", "同じフォルダーパスは登録済みです。"); return
        self.settings.roots.append(root); self.refresh()

    def edit_root(self, row=None):
        row = self.table.currentRow() if row is None else row
        if row < 0: return
        dialog = SearchRootDialog(self.settings.roots[row], self); dialog.rootReady.connect(lambda root: self._replace(row, root)); dialog.exec()

    def _replace(self, row, root):
        if any(i != row and os.path.normcase(r.path) == os.path.normcase(root.path) for i, r in enumerate(self.settings.roots)): QMessageBox.warning(self, "ファイル検索設定", "同じフォルダーパスは登録済みです。"); return
        self.settings.roots[row] = root; self.refresh()

    def delete_root(self):
        row = self.table.currentRow()
        if row >= 0 and QMessageBox.question(self, "削除の確認", "選択した検索ルートを削除しますか？") == QMessageBox.Yes: self.settings.roots.pop(row); self.refresh()

    def check_root(self):
        row = self.table.currentRow()
        if row < 0: return
        root = self.settings.roots[row]; self.check_button.setEnabled(False); self.status.setText("接続を確認中…")
        worker = _ConnectionCheck(root.id, root.path, self); self._checks[root.id] = worker; worker.finished.connect(self._check_finished); worker.finished.connect(worker.deleteLater); worker.start()

    def _check_finished(self, root_id, state):
        for row, root in enumerate(self.settings.roots):
            if root.id == root_id: self.table.setItem(row, 3, QTableWidgetItem(state)); break
        self.status.setText(state); self._update_buttons()

    def save_settings(self):
        try: self.settings.save(); self.accept()
        except OSError: QMessageBox.warning(self, "ファイル検索設定", "設定を保存できませんでした。")
