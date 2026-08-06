import logging
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox, QWidget

from .animation import load_animations
from .chat_bubble import InputBubble, chat_position, response_position
from .paths import application_root, assets_root
from .storage import constrain_to_primary, load_position, save_position


class PetWindow(QWidget):
    DRAG_THRESHOLD = 8

    def __init__(self, animations, position_path: Path):
        super().__init__()
        self.animations, self.position_path = animations, position_path
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.label = QLabel(self); self.label.setAttribute(Qt.WA_TranslucentBackground); self.label.setScaledContents(True)
        self.resize(200, 200)
        self._drag_offset = None; self._press_position = None; self._dragged = False
        self._last_activity = QTimer(self); self._last_activity.setSingleShot(True); self._last_activity.timeout.connect(lambda: self.play("sleep"))
        self._timer = QTimer(self); self._timer.timeout.connect(self._next_frame)
        self._animation = None; self._frame = 0
        self.input_bubble = InputBubble(self)
        self.play("idle"); self._last_activity.start(30000)

    def play(self, name: str):
        if name not in self.animations: return
        self._animation, self._frame = self.animations[name], 0; self._timer.stop(); self._show_frame(); self._timer.start(round(1000 / self._animation.fps))
        if name != "sleep" and not self.input_bubble.isVisible() and not self.input_bubble.pending: self._last_activity.start(30000)

    def _show_frame(self): self.label.setPixmap(self._animation.frames[self._frame].scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def _next_frame(self):
        self._frame += 1
        if self._frame >= len(self._animation.frames):
            if self._animation.name == "wave": self.play("idle"); return
            self._frame = 0 if self._animation.loop else len(self._animation.frames) - 1
        self._show_frame()
    def resizeEvent(self, event): self.label.resize(self.size()); self._show_frame() if self._animation else None
    def _activity(self):
        if self._animation.name == "sleep": self.play("idle")
        if not self.input_bubble.isVisible() and not self.input_bubble.pending: self._last_activity.start(30000)
    def open_chat(self):
        if not self.input_bubble.isVisible():
            self._show_chat_bubble()

    def close_chat(self):
        if self.input_bubble.isVisible():
            self._hide_chat_bubble()

    def toggle_chat_bubble(self):
        """Toggle visibility without touching pending API work or conversation state."""
        self._activity()
        if self.input_bubble.isVisible():
            self._hide_chat_bubble()
        else:
            self._show_chat_bubble()

    def _show_chat_bubble(self):
        self.play("wave")
        self.input_bubble.adjustSize()
        self.reposition_input_bubble()
        self.input_bubble.show(); self.input_bubble.raise_(); self.input_bubble.activateWindow()

    def reposition_input_bubble(self):
        if not hasattr(self, "input_bubble"):
            return
        screen = self.screen() or QApplication.primaryScreen()
        position = chat_position(self.frameGeometry(), screen.availableGeometry(), (self.input_bubble.width(), self.input_bubble.height()))
        direction = "top" if position.y() > self.frameGeometry().bottom() else "bottom"
        self.input_bubble.set_tail_direction(direction)
        self.input_bubble.set_tail_x(self.frameGeometry().center().x() - position.x())
        self.input_bubble.move(position)

    def _hide_chat_bubble(self):
        self.input_bubble.hide()
        if not self.input_bubble.pending:
            self.play("idle")
    def moveEvent(self, event):
        super().moveEvent(event)
        if self.input_bubble.isVisible():
            self.reposition_input_bubble()
        if self.input_bubble.response_bubble.isVisible():
            self.input_bubble._position_response()
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._activity(); self._press_position = event.globalPosition().toPoint(); self._drag_offset = self._press_position - self.pos(); self._dragged = False
    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            current = event.globalPosition().toPoint(); self._dragged = self._dragged or (current - self._press_position).manhattanLength() >= self.DRAG_THRESHOLD
            if self._dragged: self.move(current - self._drag_offset)
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            dragged = self._dragged; self._drag_offset = None; self._press_position = None
            if dragged: self.play("wave")
            else: self.toggle_chat_bubble()
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton: self.open_chat()
    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        for text, name in (("Play wave", "wave"), ("Play thinking", "thinking"), ("Play sleep", "sleep")): menu.addAction(text, lambda n=name: self.play(n))
        menu.addSeparator(); menu.addAction("チャットを開く", self.open_chat); menu.addAction("チャットを閉じる", self.close_chat); menu.addAction("Conversation history", self.input_bubble.show_history); menu.addAction("Reset position", self.reset_position); menu.addAction("Quit", QApplication.instance().quit); menu.exec(event.globalPos())
    def reset_position(self): self.move(100, 100); self._activity()
    def closeEvent(self, event): self.input_bubble.close(); save_position(self.position_path, self.pos()); super().closeEvent(event)


def main() -> int:
    root = application_root(); (root / "logs").mkdir(exist_ok=True); logging.basicConfig(filename=root / "logs" / "windows_pet.log", level=logging.INFO, encoding="utf-8")
    logging.info("startup diagnostics frozen=%s meipass=%s executable=%s cwd=%s manifest=%s", getattr(sys, "frozen", False), getattr(sys, "_MEIPASS", None), sys.executable, Path.cwd(), assets_root() / "manifest.json")
    app = QApplication(sys.argv)
    try: animations = load_animations(assets_root()); logging.info("animation assets loaded")
    except RuntimeError as exc: logging.exception("asset loading failed"); QMessageBox.critical(None, "Windows Pet", str(exc)); return 1
    window = PetWindow(animations, root / "data" / "position.json"); screen = app.primaryScreen().availableGeometry(); window.move(constrain_to_primary(load_position(window.position_path), screen, window.width())); window.show(); logging.info("pet window shown"); app.aboutToQuit.connect(window.input_bubble.close); return app.exec()


if __name__ == "__main__": raise SystemExit(main())
