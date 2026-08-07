"""Non-topmost character chooser and package distribution UI."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .character_package_loader import load_builtin_default_character, load_character_package
from .character_selection import CharacterSelection, export_wpet, import_wpet


class CharacterManagerWindow(QWidget):
    def __init__(self, pet, data_root: Path, builtin_root: Path):
        super().__init__(None)
        self.pet, self.data_root, self.builtin_root = pet, Path(data_root), Path(builtin_root)
        self.working_root, self.installed_root = self.data_root / "working", self.data_root / "installed"
        self.setWindowTitle("Character selection"); self.resize(480, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        layout = QVBoxLayout(self); self.status = QLabel(); layout.addWidget(self.status)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.content = QWidget(); self.cards = QVBoxLayout(self.content); self.cards.addStretch(); self.scroll.setWidget(self.content); layout.addWidget(self.scroll)
        buttons = QHBoxLayout(); self.import_button = QPushButton("Import .wpet"); self.close_button = QPushButton("Close")
        buttons.addWidget(self.import_button); buttons.addStretch(); buttons.addWidget(self.close_button); layout.addLayout(buttons)
        self.import_button.clicked.connect(self.import_package); self.close_button.clicked.connect(self.close); self.refresh()

    def refresh(self):
        while self.cards.count():
            item = self.cards.takeAt(0); widget = item.widget()
            if widget: widget.deleteLater()
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
        for source, package in entries: self.cards.addWidget(self._card(source, package))
        self.cards.addStretch(); current = getattr(self.pet, "current_character_package", None)
        self.status.setText(f"Active: {current.package_id if current else 'unknown'}")

    def _card(self, source, package):
        card = QWidget(); row = QHBoxLayout(card)
        thumb = QLabel(); pixmap = None
        if package.thumbnail:
            try: pixmap = load_character_package(package.package_root).animations["idle"].frames[0].pixmap
            except Exception: pass
        pixmap = pixmap or package.animations["idle"].frames[0].pixmap
        thumb.setPixmap(pixmap.scaled(96, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)); row.addWidget(thumb)
        metadata = QLabel(f"{package.name}\nID: {package.package_id}\nVersion: {package.version}\nAuthor: {package.author or '-'}\nLicense: {package.license_name or '-'}")
        row.addWidget(metadata, 1); actions = QVBoxLayout(); use = QPushButton("Use")
        use.clicked.connect(lambda: self._use(source, package)); actions.addWidget(use)
        if source != "builtin":
            export = QPushButton("Export"); export.clicked.connect(lambda: self._export(package)); actions.addWidget(export)
        if source == "installed":
            remove = QPushButton("Delete"); remove.clicked.connect(lambda: self._delete(package)); actions.addWidget(remove)
        actions.addStretch(); row.addLayout(actions); return card

    def _use(self, source, package):
        if self.pet.select_character(package, CharacterSelection(source, package.package_id, package.version)): self.refresh()
        else: QMessageBox.warning(self, "WindowsPet", "Could not activate this character.")

    def import_package(self):
        name, _ = QFileDialog.getOpenFileName(self, "Import character", "", "WindowsPet Character (*.wpet)")
        if not name: return
        try: package = import_wpet(Path(name), self.installed_root)
        except Exception: QMessageBox.warning(self, "WindowsPet", "The character package is invalid or unsafe."); return
        QMessageBox.information(self, "WindowsPet", f"Installed {package.name}."); self.refresh()

    def _export(self, package):
        name, _ = QFileDialog.getSaveFileName(self, "Export character", f"{package.package_id}.wpet", "WindowsPet Character (*.wpet)")
        if not name: return
        try: export_wpet(package, Path(name))
        except Exception: QMessageBox.warning(self, "WindowsPet", "Export failed.")

    def _delete(self, package):
        if self.pet.current_character_package and self.pet.current_character_package.package_id == package.package_id:
            QMessageBox.warning(self, "WindowsPet", "The active character cannot be deleted."); return
        if QMessageBox.question(self, "WindowsPet", f"Delete {package.name}?", QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes: return
        import shutil
        shutil.rmtree(package.package_root); self.refresh()
