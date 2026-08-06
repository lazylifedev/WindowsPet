from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class SearchResultsWindow(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("ファイル検索結果")
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("結果を絞り込む")
        layout.addWidget(self.filter)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "ファイル名", "保存場所", "種類", "更新日時", "サイズ"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.open_btn = QPushButton("保存場所を開く")
        self.copy_btn = QPushButton("パスをコピー")
        row.addWidget(self.open_btn)
        row.addWidget(self.copy_btn)
        layout.addLayout(row)
        self.status = QLabel()
        layout.addWidget(self.status)

        self.filter.textChanged.connect(self._filter)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.open_location())
        self.open_btn.clicked.connect(self.open_location)
        self.copy_btn.clicked.connect(self.copy_path)
        self._populate()

    def _populate(self):
        self.table.setRowCount(len(self.session.results))
        for i, result in enumerate(self.session.results):
            values = [
                result.result_id,
                result.name,
                f"{result.root_alias}\\{result.relative_parent}",
                result.extension,
                result.modified_at.isoformat(),
                str(result.size_bytes),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, result.result_id)
                self.table.setItem(i, column, item)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 0)
            self.table.selectRow(0)
        self._update_buttons()

    def _filter(self, text):
        query = text.casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            self.table.setRowHidden(row, query not in (item.text().casefold() if item else ""))
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                self.table.setCurrentCell(row, 0)
                self.table.selectRow(row)
                break
        self._update_buttons()

    def _update_buttons(self):
        selected = self._selected()
        enabled = selected is not None
        self.open_btn.setEnabled(enabled)
        self.copy_btn.setEnabled(enabled)

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        id_item = self.table.item(rows[0].row(), 0)
        result_id = id_item.data(Qt.UserRole) if id_item else None
        return next((result for result in self.session.results if result.result_id == result_id), None)

    def _allowed(self, path):
        normalized = os.path.normcase(os.path.abspath(path))
        return any(
            os.path.commonpath([normalized, os.path.normcase(os.path.abspath(result.full_path))])
            == os.path.normcase(os.path.abspath(result.full_path))
            for result in self.session.results
        )

    def _warning(self, message):
        QMessageBox.warning(self, "ファイル検索", message)

    def open_location(self):
        result = self._selected()
        if result is None:
            self._warning("検索結果を選択してください。")
            return
        path = Path(os.path.normpath(result.full_path))
        if not self._allowed(str(path)):
            self._warning("許可された検索ルート外です。")
            return
        if not path.exists():
            parent = path.parent
            if not parent.exists():
                self._warning("保存場所が見つかりません。")
                return
            answer = QMessageBox.question(
                self,
                "ファイル検索",
                "ファイルが移動または削除されています。\n保存されていたフォルダーを開きますか？",
            )
            if answer != QMessageBox.Yes:
                return
            path = parent
        args = ["explorer.exe", f"/select,{path}"] if path.is_file() else ["explorer.exe", str(path)]
        try:
            subprocess.Popen(args, shell=False)
        except OSError:
            self._warning("保存場所を開けませんでした。")

    def copy_path(self):
        result = self._selected()
        if result is None:
            self._warning("検索結果を選択してください。")
            return
        QApplication.clipboard().setText(result.full_path)
        self.status.setText("パスをコピーしました。")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.table.hasFocus():
            self.open_location()
            event.accept()
            return
        super().keyPressEvent(event)
