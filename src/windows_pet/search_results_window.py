from __future__ import annotations
import os, subprocess
from pathlib import Path
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLineEdit,QTableWidget,QTableWidgetItem,QPushButton,QHBoxLayout,QMessageBox,QApplication

class SearchResultsWindow(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent); self.session=session; self.setWindowTitle('ファイル検索結果'); self.resize(760,480)
        layout=QVBoxLayout(self); self.filter=QLineEdit(); self.filter.setPlaceholderText('結果を絞り込む'); layout.addWidget(self.filter)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(['ID','ファイル名','保存場所','種類','更新日時','サイズ']); self.table.setSelectionBehavior(QTableWidget.SelectRows); layout.addWidget(self.table)
        row=QHBoxLayout(); open_btn=QPushButton('保存場所を開く'); copy_btn=QPushButton('パスをコピー'); row.addWidget(open_btn); row.addWidget(copy_btn); layout.addLayout(row)
        self.filter.textChanged.connect(self._filter); open_btn.clicked.connect(self.open_location); copy_btn.clicked.connect(self.copy_path); self._populate()
    def _populate(self):
        self.table.setRowCount(len(self.session.results))
        for i,r in enumerate(self.session.results):
            vals=[r.result_id,r.name, f'{r.root_alias}\\{r.relative_parent}',r.extension,r.modified_at.isoformat(),str(r.size_bytes)]
            for j,v in enumerate(vals): self.table.setItem(i,j,QTableWidgetItem(v))
    def _filter(self,text):
        for i in range(self.table.rowCount()): self.table.setRowHidden(i, text.casefold() not in self.table.item(i,1).text().casefold())
    def _selected(self):
        rows=self.table.selectionModel().selectedRows(); return self.session.results[rows[0].row()] if rows else None
    def _allowed(self,path):
        p=os.path.normcase(os.path.abspath(path)); return any(os.path.commonpath([p,os.path.normcase(os.path.abspath(r.full_path))]) == os.path.normcase(os.path.abspath(r.full_path)) for r in self.session.results)
    def open_location(self):
        r=self._selected()
        if not r or not Path(r.full_path).exists(): QMessageBox.warning(self,'ファイル検索','ファイルが見つかりません。'); return
        if not self._allowed(r.full_path): QMessageBox.warning(self,'ファイル検索','許可された検索ルート外です。'); return
        args=['explorer.exe','/select,',r.full_path] if Path(r.full_path).is_file() else ['explorer.exe',r.full_path]; subprocess.Popen(args, shell=False)
    def copy_path(self):
        r=self._selected()
        if r: QApplication.clipboard().setText(r.full_path)
