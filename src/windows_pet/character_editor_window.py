"""Local-only character editor, including safe frame drag and drop."""
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout,
                               QLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from .character_editor_model import EditableCharacter, EditableFrame
from .character_models import CharacterPackage, PlaybackMode
from .character_package_loader import load_character_package
from .character_working_package import (EditorImageError, create_working_from_builtin,
                                        save_working_package, validate_png)

FRAME_MIME = "application/x-windowspet-character-frame"
DEFAULT_DURATION_MS = 150
MAX_FRAMES = 10


def natural_filename_key(path: Path):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def validate_batch_capacity(existing: int, additions: int) -> bool:
    return existing + additions <= MAX_FRAMES


def calculate_insert_index(card_centers: list[int], cursor_x: int) -> int:
    """Return the insertion slot before the first card whose center is right of x."""
    return next((index for index, center in enumerate(card_centers) if cursor_x < center), len(card_centers))


def move_frame(frames: list[EditableFrame], source_index: int, insert_index: int) -> bool:
    """Move one object without changing identity; return false for a same-slot drop."""
    if not 0 <= source_index < len(frames):
        return False
    destination = insert_index - (1 if insert_index > source_index else 0)
    if destination == source_index:
        return False
    frame = frames.pop(source_index)
    frames.insert(destination, frame)
    return True


class FrameDragHandle(QLabel):
    def __init__(self, strip, animation, frame, parent=None):
        super().__init__("⠿", parent)
        self.strip, self.animation, self.frame, self.origin = strip, animation, frame, None
        self.setAccessibleName("フレームを並べ替え")
        self.setToolTip("ドラッグして再生順を並べ替え")
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.origin or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        if (event.position().toPoint() - self.origin).manhattanLength() < QApplication.startDragDistance():
            return
        self.origin = None
        self.strip.start_frame_drag(self.animation, self.frame)


class FrameDropIndicator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(3)
        self.setStyleSheet("background: #3388dd;")
        self.hide()


class AnimationFrameStrip(QScrollArea):
    def __init__(self, editor, animation, parent=None):
        super().__init__(parent)
        self.editor, self.animation = editor, animation
        self.setWidgetResizable(False); self.setAcceptDrops(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.holder = QWidget(); self.cards = QHBoxLayout(self.holder)
        self.cards.setAlignment(Qt.AlignLeft); self.cards.setSizeConstraint(QLayout.SetMinimumSize)
        self.indicator = FrameDropIndicator(self.holder)
        self.auto_scroll = QTimer(self); self.auto_scroll.setInterval(40); self.auto_scroll.timeout.connect(self._auto_scroll_tick)
        self._scroll_direction = 0
        self.setWidget(self.holder)

    def rebuild(self):
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget() and item.widget() is not self.indicator:
                item.widget().deleteLater()
        for index, frame in enumerate(self.animation.frames):
            self.cards.addWidget(self.editor._card(self, self.animation, frame, index))
        add = QPushButton("＋")
        add.setAccessibleName("画像を追加"); add.setToolTip("PNG画像を追加")
        add.setEnabled(len(self.animation.frames) < MAX_FRAMES); add.setFixedSize(56, 56)
        add.clicked.connect(lambda: self.editor.add_frames_from_dialog(self.animation))
        self.cards.addWidget(add, alignment=Qt.AlignVCenter)
        height = max((self.editor._card_height(self.cards.itemAt(i).widget()) for i in range(len(self.animation.frames))), default=56)
        self.setMinimumHeight(height + self.horizontalScrollBar().sizeHint().height() + self.frameWidth() * 2)

    def start_frame_drag(self, animation, frame):
        if self.editor.recovery_mode:
            return
        payload = json.dumps({"session": self.editor.session_token, "eventId": animation.event_id, "frameId": frame.frame_id}, separators=(",", ":")).encode()
        mime = QMimeData(); mime.setData(FRAME_MIME, QByteArray(payload))
        drag = QDrag(self); drag.setMimeData(mime)
        drag.setPixmap(frame.preview_pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.MoveAction)

    def _valid_internal(self, mime):
        if not mime.hasFormat(FRAME_MIME): return None
        try:
            data = json.loads(bytes(mime.data(FRAME_MIME)).decode("utf-8"))
            if set(data) != {"session", "eventId", "frameId"} or data["session"] != self.editor.session_token: return None
            if data["eventId"] != self.animation.event_id: return None
            frame = next((f for f in self.animation.frames if f.frame_id == data["frameId"]), None)
            return frame
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError): return None

    def _external_paths(self, mime):
        if not mime.hasUrls(): return None
        paths = []
        for url in mime.urls():
            if not url.isLocalFile(): return None
            paths.append(Path(url.toLocalFile()))
        return paths or None

    def _insert_index(self, position: QPoint):
        return calculate_insert_index([self.cards.itemAt(i).widget().geometry().center().x() for i in range(len(self.animation.frames))], position.x() + self.horizontalScrollBar().value())

    def _show_indicator(self, index):
        if index < len(self.animation.frames): x = self.cards.itemAt(index).widget().geometry().left() - 2
        elif self.animation.frames: x = self.cards.itemAt(len(self.animation.frames) - 1).widget().geometry().right() + 3
        else: x = 4
        self.indicator.setGeometry(x, 6, 3, max(1, self.holder.height() - 12)); self.indicator.show()

    def _hide_indicator(self):
        self.indicator.hide(); self.auto_scroll.stop(); self._scroll_direction = 0

    def _update_auto_scroll(self, x):
        self._scroll_direction = -1 if x < 40 else 1 if x > self.viewport().width() - 40 else 0
        if self._scroll_direction and self.horizontalScrollBar().maximum() > self.horizontalScrollBar().minimum(): self.auto_scroll.start()
        else: self.auto_scroll.stop()

    def _auto_scroll_tick(self):
        bar = self.horizontalScrollBar(); bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + self._scroll_direction * 20)))

    def dragEnterEvent(self, event):
        if self.editor.recovery_mode: event.ignore(); return
        internal, paths = self._valid_internal(event.mimeData()), self._external_paths(event.mimeData())
        if internal or paths: event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        internal, paths = self._valid_internal(event.mimeData()), self._external_paths(event.mimeData())
        if not (internal or paths): self._hide_indicator(); event.ignore(); return
        if paths and not validate_batch_capacity(len(self.animation.frames), len(paths)):
            self._hide_indicator(); event.ignore(); return
        index = self._insert_index(event.position().toPoint()); self._show_indicator(index); self._update_auto_scroll(event.position().x()); event.acceptProposedAction()

    def dragLeaveEvent(self, event): self._hide_indicator(); event.accept()

    def dropEvent(self, event):
        self._hide_indicator()
        if self.editor.recovery_mode: event.ignore(); return
        index = self._insert_index(event.position().toPoint()); internal = self._valid_internal(event.mimeData())
        if internal:
            changed = move_frame(self.animation.frames, self.animation.frames.index(internal), index)
            if changed: self.editor._set_dirty(True); self.editor._render()
            event.acceptProposedAction(); return
        paths = self._external_paths(event.mimeData())
        if paths and self.editor.add_external_frames(self.animation, paths, index): event.acceptProposedAction()
        else: event.ignore()

    def closeEvent(self, event): self._hide_indicator(); super().closeEvent(event)


class CharacterEditorWindow(QDialog):
    def __init__(self, builtin: CharacterPackage, working_root: Path, parent=None):
        super().__init__(parent); self.setWindowTitle("キャラクターアニメーション設定"); self.resize(1000, 700); self.setMinimumSize(700, 500)
        self.builtin, self.working_root, self.model, self.dirty, self._rows = builtin, Path(working_root), None, False, []
        self.recovery_mode = False; self.session_token = uuid4().hex; self.session = self.working_root.parent / f".editor-session-{self.session_token}"; self.session.mkdir(parents=True, exist_ok=False)
        self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True); self.preview_timer.timeout.connect(self._next_preview); self.preview_animation = None; self.preview_index = 0
        layout = QVBoxLayout(self); self.status = QLabel(); layout.addWidget(self.status)
        self.preview = QLabel(); self.preview.setAlignment(Qt.AlignCenter); self.preview.setFixedHeight(150); self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed); layout.addWidget(self.preview)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.content = QWidget(); self.content_layout = QVBoxLayout(self.content); self.content_layout.setSizeConstraint(QLayout.SetMinimumSize); self.scroll.setWidget(self.content); layout.addWidget(self.scroll)
        actions = QHBoxLayout(); actions.addStretch(); self.rebuild_button, self.reload_button, self.cancel_button, self.save_button = QPushButton("既定から作り直す"), QPushButton("再読み込み"), QPushButton("キャンセル"), QPushButton("保存")
        self.rebuild_button.setVisible(False)
        for button in (self.rebuild_button, self.reload_button, self.cancel_button, self.save_button): actions.addWidget(button)
        layout.addLayout(actions); self.rebuild_button.clicked.connect(self.rebuild_from_builtin); self.reload_button.clicked.connect(self.reload); self.cancel_button.clicked.connect(self.close); self.save_button.clicked.connect(self.save); self._load_initial()

    def _load_initial(self):
        try: package = load_character_package(self.working_root) if self.working_root.exists() else create_working_from_builtin(self.builtin, self.working_root)
        except Exception:
            if self.working_root.exists(): self._show_builtin_fallback(); return
            self.status.setText("編集データを読み込めません"); self.save_button.setEnabled(False); return
        self._show_package(package)
    def _show_package(self, package): self.recovery_mode = False; self.rebuild_button.setVisible(False); self.model = EditableCharacter.from_package(package); self._set_dirty(False); self._render()
    def _show_builtin_fallback(self): self.recovery_mode = True; self.rebuild_button.setVisible(True); self.model = EditableCharacter.from_package(self.builtin); self._set_dirty(False); self._render(); self.status.setText("編集データを読み込めません。既定キャラクターを表示しています。")
    def _set_dirty(self, value): self.dirty = value; self.save_button.setEnabled(value and self.model is not None and not self.recovery_mode)
    def _render(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0); widget = item.widget(); widget and widget.deleteLater()
        self._rows, self._frame_scroll_areas = [], []
        for animation in self.model.animations:
            row = QFrame(); row.setFrameShape(QFrame.StyledPanel); box = QVBoxLayout(row); header = QHBoxLayout(); header.addWidget(QLabel(f"{animation.event_id}    {'必須' if animation.required else '任意'}    {animation.playback.value}    {len(animation.frames)} / 10"))
            play, stop = QPushButton("▶ プレビュー"), QPushButton("■ 停止"); play.clicked.connect(lambda _, a=animation: self.start_preview(a)); stop.clicked.connect(self.stop_preview); header.addStretch(); header.addWidget(play); header.addWidget(stop); box.addLayout(header)
            strip = AnimationFrameStrip(self, animation); strip.rebuild(); box.addWidget(strip)
            row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed); row.setMinimumHeight(row.sizeHint().height()); row.setMaximumHeight(row.minimumHeight()); self.content_layout.addWidget(row); self._rows.append(row); self._frame_scroll_areas.append(strip)
        self.content_layout.addStretch()
    def _card(self, strip, animation, frame, index):
        card = QFrame(); card.setFrameShape(QFrame.StyledPanel); card.setFixedWidth(150); card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed); box = QVBoxLayout(card); box.setSizeConstraint(QLayout.SetMinimumSize)
        top = QHBoxLayout(); top.addWidget(FrameDragHandle(strip, animation, frame)); top.addWidget(QLabel(f"frame {index + 1}")); top.addStretch(); box.addLayout(top)
        image = QLabel(); image.setAlignment(Qt.AlignCenter); image.setFixedSize(110, 110); image.setPixmap(frame.preview_pixmap.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)); box.addWidget(image, alignment=Qt.AlignHCenter)
        duration = QSpinBox(); duration.setRange(50, 5000); duration.setSingleStep(50); duration.setSuffix(" ms"); duration.setValue(frame.duration_ms); duration.setAccessibleName(f"frame {index + 1} duration"); duration.valueChanged.connect(lambda value, f=frame: self._duration(f, value)); box.addWidget(duration)
        replace, remove = QPushButton("差し替え"), QPushButton("削除"); replace.setAccessibleName("画像を差し替え"); replace.clicked.connect(lambda: self.replace_frame(frame)); remove.setAccessibleName("フレームを削除"); remove.setEnabled(len(animation.frames) > 2); remove.clicked.connect(lambda: self.delete_frame(animation, frame)); box.addWidget(replace); box.addWidget(remove); card.setFixedHeight(card.sizeHint().height()); return card
    @staticmethod
    def _card_height(card): return card.sizeHint().height()
    def _duration(self, frame, value):
        if frame.duration_ms != value: frame.duration_ms = value; self._set_dirty(True)
    def _select_pngs(self):
        names, _ = QFileDialog.getOpenFileNames(self, "PNG画像を選択", "", "PNG画像 (*.png)"); return [Path(name) for name in names]
    def _stage_batch(self, sources):
        ordered = sorted(sources, key=natural_filename_key); batch = self.session / f"batch-{uuid4().hex}"; staged = []
        try:
            for source in ordered: validate_png(source)
            batch.mkdir()
            for source in ordered:
                target = batch / f"{uuid4().hex}.png"; shutil.copyfile(source, target); pixmap = validate_png(target); staged.append((target, pixmap))
            return staged
        except Exception:
            if batch.exists(): shutil.rmtree(batch)
            raise EditorImageError()
    def _insert_sources(self, animation, sources, index):
        if not validate_batch_capacity(len(animation.frames), len(sources)): return False
        try: staged = self._stage_batch(sources)
        except EditorImageError: QMessageBox.warning(self, "WindowsPet", "画像を追加できません。\nすべてのPNG画像を確認してください。"); return False
        frames = []
        for path, pixmap in staged:
            frame_id = self.model.frame_id_for(animation.event_id)
            frames.append(EditableFrame(frame_id, f"animations/{animation.event_id}/{frame_id}.png", DEFAULT_DURATION_MS, pixmap, path))
        animation.frames[index:index] = frames; self._set_dirty(True); self._render(); return True
    def add_external_frames(self, animation, paths, index): return self._insert_sources(animation, paths, index)
    def add_frames_from_dialog(self, animation):
        paths = self._select_pngs()
        if paths: self._insert_sources(animation, paths, len(animation.frames))
    def add_frame(self, animation): self.add_frames_from_dialog(animation)
    def delete_frame(self, animation, frame):
        if len(animation.frames) <= 2: QMessageBox.warning(self, "WindowsPet", "1イベントには最低2コマ必要です。"); return
        animation.frames.remove(frame); self._set_dirty(True); self._render()
    def replace_frame(self, frame):
        sources = self._select_pngs()
        if len(sources) != 1: return
        try: path, pixmap = self._stage_batch(sources)[0]
        except EditorImageError: QMessageBox.warning(self, "WindowsPet", "画像を追加できません。\nPNG画像を確認してください。"); return
        frame.source_path, frame.preview_pixmap = path, pixmap; self._set_dirty(True); self._render()
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
        if self.recovery_mode: return
        try: package = save_working_package(self.model, self.working_root)
        except Exception: QMessageBox.warning(self, "WindowsPet", "保存できませんでした。\n現在の保存内容は変更されていません。"); return
        self._show_package(package); QMessageBox.information(self, "WindowsPet", "保存しました。")
    def rebuild_from_builtin(self):
        if QMessageBox.question(self, "WindowsPet", "保存済みの編集データを破棄して、既定キャラクターから作り直しますか？", QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes: return
        try: create_working_from_builtin(self.builtin, self.working_root); package = load_character_package(self.working_root)
        except Exception: self._show_builtin_fallback(); return
        self._show_package(package)
    def reload(self):
        if self.dirty and QMessageBox.question(self, "WindowsPet", "未保存の変更を破棄して再読み込みしますか？", QMessageBox.Discard | QMessageBox.Cancel) != QMessageBox.Discard: return
        try: package = load_character_package(self.working_root)
        except Exception: self._show_builtin_fallback(); return
        self._show_package(package)
    def closeEvent(self, event):
        if self.dirty and QMessageBox.question(self, "WindowsPet", "変更内容を破棄しますか？", QMessageBox.Discard | QMessageBox.Cancel) != QMessageBox.Discard: event.ignore(); return
        self.stop_preview()
        if self.session.exists(): shutil.rmtree(self.session)
        event.accept()
