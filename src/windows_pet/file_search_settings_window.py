from __future__ import annotations
import os, uuid
from pathlib import Path
from PySide6.QtWidgets import QDialog,QVBoxLayout,QTableWidget,QTableWidgetItem,QPushButton,QHBoxLayout,QFileDialog,QInputDialog,QMessageBox
from .file_search_settings import SearchSettings
from .file_search_models import SearchRoot

class FileSearchSettingsWindow(QDialog):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent); self.settings=settings or SearchSettings.load(); self.setWindowTitle('ファイル検索設定'); self.resize(760,400)
        layout=QVBoxLayout(self); self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(['有効','エイリアス','パス','状態']); layout.addWidget(self.table)
        row=QHBoxLayout();
        for text,slot in [('追加',self.add_root),('編集',self.edit_root),('削除',self.delete_root),('接続確認',self.check_root),('保存',self.save_settings),('閉じる',self.close)]:
            b=QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        layout.addLayout(row); self.refresh()
    def refresh(self):
        self.table.setRowCount(len(self.settings.roots))
        for i,r in enumerate(self.settings.roots):
            self.table.setItem(i,0,QTableWidgetItem('✓' if r.enabled else '')); self.table.setItem(i,1,QTableWidgetItem(r.alias)); self.table.setItem(i,2,QTableWidgetItem(r.path)); self.table.setItem(i,3,QTableWidgetItem('確認待ち'))
    def _input(self,current=None):
        alias,ok=QInputDialog.getText(self,'検索ルート','エイリアス:',text=current.alias if current else '')
        if not ok or not alias.strip(): return None
        path,ok=QInputDialog.getText(self,'検索ルート','フォルダパス:',text=current.path if current else '')
        if not ok or not path.strip(): return None
        if not path.strip().startswith('\\\\'):
            chosen=QFileDialog.getExistingDirectory(self,'フォルダを選択',path.strip())
            if chosen: path=chosen
        return alias.strip(),os.path.normpath(path.strip())
    def add_root(self):
        data=self._input()
        if not data:return
        if any(os.path.normcase(r.path)==os.path.normcase(data[1]) for r in self.settings.roots): QMessageBox.warning(self,'ファイル検索','同じパスは登録できません。'); return
        self.settings.roots.append(SearchRoot(uuid.uuid4().hex,' '.join(data[0].split()),data[1],True)); self.refresh()
    def edit_root(self):
        i=self.table.currentRow()
        if i<0:return
        data=self._input(self.settings.roots[i])
        if data:self.settings.roots[i]=SearchRoot(self.settings.roots[i].id,data[0],data[1],self.settings.roots[i].enabled); self.refresh()
    def delete_root(self):
        i=self.table.currentRow()
        if i>=0 and QMessageBox.question(self,'確認','検索ルート設定だけを削除します。実フォルダは削除しません。') == QMessageBox.Yes: self.settings.roots.pop(i); self.refresh()
    def check_root(self):
        i=self.table.currentRow()
        if i<0:return
        p=Path(self.settings.roots[i].path)
        try: ok=p.is_dir(); list(os.scandir(p)) if ok else None
        except PermissionError: ok=False
        self.table.setItem(i,3,QTableWidgetItem('接続可能' if ok else 'アクセス不可'))
    def save_settings(self):
        try:self.settings.save(); QMessageBox.information(self,'ファイル検索','設定を保存しました。')
        except OSError: QMessageBox.warning(self,'ファイル検索','設定を保存できませんでした。')
