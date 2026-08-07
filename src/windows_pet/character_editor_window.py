"""Basic, local-only editor for schema-v1 character animation packages."""
import shutil
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .character_editor_model import EditableCharacter, EditableFrame
from .character_models import CharacterPackage, PlaybackMode
from .character_package_loader import load_character_package
from .character_working_package import EditorImageError, create_working_from_builtin, save_working_package, validate_png


class CharacterEditorWindow(QDialog):
    def __init__(self, builtin: CharacterPackage, working_root: Path, parent=None):
        super().__init__(parent); self.setWindowTitle("キャラクターアニメーション設定"); self.resize(1000, 700); self.setMinimumSize(700, 500)
        self.builtin, self.working_root = builtin, Path(working_root); self.model = None; self.dirty = False; self._rows = []
        self.session = self.working_root.parent / f".editor-session-{uuid4().hex}"; self.session.mkdir(parents=True, exist_ok=False)
        self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True); self.preview_timer.timeout.connect(self._next_preview)
        self.preview_animation = None; self.preview_index = 0
        layout = QVBoxLayout(self); self.status = QLabel(); layout.addWidget(self.status)
        self.preview = QLabel(); self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumHeight(130); layout.addWidget(self.preview)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.content = QWidget(); self.content_layout = QVBoxLayout(self.content); self.scroll.setWidget(self.content); layout.addWidget(self.scroll)
        actions = QHBoxLayout(); actions.addStretch(); self.reload_button = QPushButton("再読み込み"); self.cancel_button = QPushButton("キャンセル"); self.save_button = QPushButton("保存")
        actions.addWidget(self.reload_button); actions.addWidget(self.cancel_button); actions.addWidget(self.save_button); layout.addLayout(actions)
        self.reload_button.clicked.connect(self.reload); self.cancel_button.clicked.connect(self.close); self.save_button.clicked.connect(self.save)
        self._load_initial()

    def _load_initial(self):
        try:
            package = load_character_package(self.working_root) if self.working_root.exists() else create_working_from_builtin(self.builtin, self.working_root)
        except Exception:
            self.status.setText("編集データを読み込めません"); self.save_button.setEnabled(False); return
        self.model = EditableCharacter.from_package(package); self._set_dirty(False); self._render()

    def _set_dirty(self, value): self.dirty = value; self.save_button.setEnabled(value and self.model is not None)
    def _render(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0); widget = item.widget(); widget and widget.deleteLater()
        self._rows = []
        for animation in self.model.animations:
            row = QFrame(); row.setFrameShape(QFrame.StyledPanel); box = QVBoxLayout(row); header = QHBoxLayout()
            header.addWidget(QLabel(f"{animation.event_id}    {'必須' if animation.required else '任意'}    {animation.playback.value}    {len(animation.frames)} / 10"))
            play = QPushButton("▶ プレビュー"); stop = QPushButton("■ 停止"); play.clicked.connect(lambda _, a=animation: self.start_preview(a)); stop.clicked.connect(self.stop_preview); header.addStretch(); header.addWidget(play); header.addWidget(stop); box.addLayout(header)
            horizontal = QScrollArea(); horizontal.setWidgetResizable(True); holder = QWidget(); cards = QHBoxLayout(holder); cards.setAlignment(Qt.AlignLeft)
            for index, frame in enumerate(animation.frames): cards.addWidget(self._card(animation, frame, index))
            add = QPushButton("＋"); add.setAccessibleName("画像を追加"); add.setToolTip("PNG画像を追加"); add.setEnabled(len(animation.frames) < 10); add.clicked.connect(lambda _, a=animation: self.add_frame(a)); cards.addWidget(add); horizontal.setWidget(holder); box.addWidget(horizontal)
            self.content_layout.addWidget(row); self._rows.append(row)
        self.content_layout.addStretch()
    def _card(self, animation, frame, index):
        card = QFrame(); card.setFrameShape(QFrame.StyledPanel); box = QVBoxLayout(card); image = QLabel(); image.setAlignment(Qt.AlignCenter); image.setPixmap(frame.preview_pixmap.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)); box.addWidget(image); box.addWidget(QLabel(f"frame {index + 1}"))
        duration = QSpinBox(); duration.setRange(50, 5000); duration.setSingleStep(50); duration.setSuffix(" ms"); duration.setValue(frame.duration_ms); duration.setAccessibleName(f"frame {index + 1} duration")
        duration.valueChanged.connect(lambda value, f=frame: self._duration(f, value)); box.addWidget(duration)
        replace = QPushButton("差し替え"); replace.setAccessibleName("画像を差し替え"); replace.clicked.connect(lambda _, f=frame: self.replace_frame(f)); remove = QPushButton("削除"); remove.setAccessibleName("フレームを削除"); remove.setEnabled(len(animation.frames) > 2); remove.clicked.connect(lambda _, a=animation, f=frame: self.delete_frame(a, f)); box.addWidget(replace); box.addWidget(remove); return card
    def _duration(self, frame, value):
        if frame.duration_ms != value: frame.duration_ms = value; self._set_dirty(True)
    def _select_png(self):
        name, _ = QFileDialog.getOpenFileName(self, "PNG画像を選択", "", "PNG画像 (*.png)"); return Path(name) if name else None
    def _session_copy(self, source):
        pixmap = validate_png(source); target = self.session / f"{uuid4().hex}.png"; shutil.copyfile(source, target); validate_png(target); return target, pixmap
    def add_frame(self, animation):
        if len(animation.frames) >= 10: QMessageBox.warning(self, "WindowsPet", "このイベントには最大10コマまで設定できます。"); return
        source = self._select_png()
        if source is None: return
        try: copied, pixmap = self._session_copy(source)
        except EditorImageError: QMessageBox.warning(self, "WindowsPet", "画像を追加できません。\nPNG画像を確認してください。"); return
        frame_id = self.model.frame_id_for(animation.event_id); animation.frames.append(EditableFrame(frame_id, f"animations/{animation.event_id}/{frame_id}.png", 150, pixmap, copied)); self._set_dirty(True); self._render()
    def delete_frame(self, animation, frame):
        if len(animation.frames) <= 2: QMessageBox.warning(self, "WindowsPet", "1イベントには最低2コマ必要です。"); return
        animation.frames.remove(frame); self._set_dirty(True); self._render()
    def replace_frame(self, frame):
        source = self._select_png()
        if source is None: return
        try: copied, pixmap = self._session_copy(source)
        except EditorImageError: QMessageBox.warning(self, "WindowsPet", "画像を追加できません。\nPNG画像を確認してください。"); return
        frame.source_path, frame.preview_pixmap = copied, pixmap; self._set_dirty(True); self._render()
    def start_preview(self, animation): self.preview_animation, self.preview_index = animation, 0; self._show_preview()
    def _show_preview(self):
        frame = self.preview_animation.frames[self.preview_index]; self.preview.setPixmap(frame.preview_pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)); self.preview_timer.start(frame.duration_ms)
    def _next_preview(self):
        if self.preview_animation is None: return
        if self.preview_index + 1 >= len(self.preview_animation.frames):
            if self.preview_animation.playback is PlaybackMode.ONCE: self.stop_preview(); return
            self.preview_index = 0
        else: self.preview_index += 1
        self._show_preview()
    def stop_preview(self): self.preview_timer.stop(); self.preview_animation = None
    def save(self):
        try: package = save_working_package(self.model, self.working_root)
        except Exception: QMessageBox.warning(self, "WindowsPet", "保存できませんでした。\n現在の保存内容は変更されていません。"); return
        self.model = EditableCharacter.from_package(package); self._set_dirty(False); self._render(); QMessageBox.information(self, "WindowsPet", "保存しました。")
    def reload(self):
        if self.dirty and QMessageBox.question(self, "WindowsPet", "未保存の変更を破棄して再読み込みしますか？", QMessageBox.Discard | QMessageBox.Cancel) != QMessageBox.Discard: return
        try: package = load_character_package(self.working_root)
        except Exception: QMessageBox.warning(self, "WindowsPet", "保存済みデータを読み込めません。"); return
        self.model = EditableCharacter.from_package(package); self._set_dirty(False); self._render()
    def closeEvent(self, event):
        if self.dirty and QMessageBox.question(self, "WindowsPet", "変更内容を破棄しますか？", QMessageBox.Discard | QMessageBox.Cancel) != QMessageBox.Discard: event.ignore(); return
        self.stop_preview();
        if self.session.exists(): shutil.rmtree(self.session)
        event.accept()
