"""Non-topmost character chooser and package distribution UI."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .character_package_loader import load_builtin_default_character, load_character_package
from .character_selection import CharacterSelection, export_wpet, import_wpet, inspect_wpet


class CharacterManagerWindow(QWidget):
    def __init__(self, pet, data_root: Path, builtin_root: Path):
        super().__init__(None)
        self.pet, self.data_root, self.builtin_root = pet, Path(data_root), Path(builtin_root)
        self.working_root, self.installed_root = self.data_root / "working", self.data_root / "installed"
        self.setWindowTitle("キャラクター選択"); self.resize(480, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        layout = QVBoxLayout(self); self.status = QLabel(); layout.addWidget(self.status)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.content = QWidget(); self.cards = QVBoxLayout(self.content); self.cards.addStretch(); self.scroll.setWidget(self.content); layout.addWidget(self.scroll)
        buttons = QHBoxLayout(); self.import_button = QPushButton(".wpet をインポート"); self.close_button = QPushButton("閉じる")
        buttons.addWidget(self.import_button); buttons.addStretch(); buttons.addWidget(self.close_button); layout.addLayout(buttons)
        self.import_button.clicked.connect(self.import_package); self.close_button.clicked.connect(self.close); self.refresh()

    def refresh(self):
        while self.cards.count():
            item = self.cards.takeAt(0); widget = item.widget()
            if widget: widget.deleteLater()
        entries = self._entries()
        for source, package in entries: self.cards.addWidget(self._card(source, package))
        self.cards.addStretch(); current = getattr(self.pet, "current_character_package", None)
        self.status.setText(f"使用中: {current.package_id if current else '不明'}")

    def _card(self, source, package):
        card = QWidget(); row = QHBoxLayout(card)
        thumb = QLabel(); pixmap = None
        pixmap = package.thumbnail_pixmap or package.animations["idle"].frames[0].pixmap
        thumb.setPixmap(pixmap.scaled(96, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)); row.addWidget(thumb)
        source_label = {"builtin": "内蔵", "working": "編集中", "installed": "インストール済み"}[source]
        metadata = QLabel(f"{package.name}\n状態: {source_label}\nID: {package.package_id}\nバージョン: {package.version}\n作者: {package.author or '-'}\nライセンス: {package.license_name or '-'}")
        row.addWidget(metadata, 1); actions = QVBoxLayout(); use = QPushButton("使用する")
        use.clicked.connect(lambda: self._use(source, package)); actions.addWidget(use)
        if source != "builtin":
            export = QPushButton("エクスポート"); export.clicked.connect(lambda: self._export(package)); actions.addWidget(export)
        if source == "installed":
            remove = QPushButton("削除"); remove.clicked.connect(lambda: self._delete(package)); actions.addWidget(remove)
        actions.addStretch(); row.addLayout(actions); return card

    def _use(self, source, package):
        if self.pet.select_character(package, CharacterSelection(source, package.package_id, package.version)): self.refresh()
        else: QMessageBox.warning(self, "WindowsPet", "このキャラクターを使用できませんでした。")

    def import_package(self):
        name, _ = QFileDialog.getOpenFileName(self, "キャラクターをインポート", "", "WindowsPet Character (*.wpet)")
        if not name: return
        try: package = import_wpet(Path(name), self.installed_root)
        except FileExistsError:
            try: candidate = inspect_wpet(Path(name), self.installed_root)
            except Exception: QMessageBox.warning(self, "WindowsPet", "キャラクターパッケージが無効または安全でありません。"); return
            installed = next((p for source, p in self._entries() if source == "installed" and p.package_id == candidate.package_id), None)
            old = f"{installed.name}\nバージョン: {installed.version}" if installed else candidate.package_id
            new = f"{candidate.name}\nバージョン: {candidate.version}"
            if QMessageBox.question(self, "更新の確認", f"同じ ID のキャラクターがインストール済みです。\n\n現在:\n{old}\n\n読み込み:\n{new}\n\n更新しますか？", QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes: return
            try: package = import_wpet(Path(name), self.installed_root, replace_existing=True)
            except Exception: QMessageBox.warning(self, "WindowsPet", "更新に失敗しました。既存のキャラクターは復元されました。"); return
        except Exception: QMessageBox.warning(self, "WindowsPet", "キャラクターパッケージが無効または安全でありません。"); return
        QMessageBox.information(self, "WindowsPet", f"{package.name} をインストールしました。"); self.refresh()

    def _entries(self):
        entries = [("builtin", load_builtin_default_character(self.builtin_root))]
        try: entries.append(("working", load_character_package(self.working_root)))
        except Exception: pass
        if self.installed_root.exists():
            for path in sorted(self.installed_root.iterdir()):
                if path.is_dir() and not path.is_symlink():
                    try:
                        package = load_character_package(path)
                        if package.package_id == path.name: entries.append(("installed", package))
                    except Exception: continue
        return entries

    def _export(self, package):
        name, _ = QFileDialog.getSaveFileName(self, "キャラクターをエクスポート", f"{package.package_id}.wpet", "WindowsPet Character (*.wpet)")
        if not name: return
        try: export_wpet(package, Path(name))
        except Exception: QMessageBox.warning(self, "WindowsPet", "Export failed.")

    def _delete(self, package):
        if self.pet.current_character_selection and self.pet.current_character_selection.source == "installed" and self.pet.current_character_selection.package_id == package.package_id:
            QMessageBox.warning(self, "WindowsPet", "使用中のキャラクターは削除できません。"); return
        if QMessageBox.question(self, "WindowsPet", f"{package.name} を削除しますか？", QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes: return
        import shutil
        root, installed = package.package_root.resolve(), self.installed_root.resolve()
        if package.package_root.is_symlink() or root.parent != installed or root.name != package.package_id or not root.is_dir():
            QMessageBox.warning(self, "WindowsPet", "安全な削除対象ではありません。"); return
        try: load_character_package(root)
        except Exception:
            QMessageBox.warning(self, "WindowsPet", "無効なパッケージは削除できません。"); return
        shutil.rmtree(root); self.refresh()
