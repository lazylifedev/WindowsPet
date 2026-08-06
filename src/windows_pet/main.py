import logging
import sys
from pathlib import Path
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget, QMessageBox
from .animation import load_animations
from .paths import application_root, assets_root
from .storage import constrain_to_primary, load_position, save_position

class PetWindow(QWidget):
    def __init__(self, animations, position_path: Path):
        super().__init__()
        self.animations, self.position_path = animations, position_path
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.label = QLabel(self); self.label.setAttribute(Qt.WA_TranslucentBackground)
        self.label.setScaledContents(True)
        self.resize(200, 200)
        self._drag_offset = None; self._last_activity = QTimer(self); self._last_activity.setSingleShot(True)
        self._last_activity.timeout.connect(lambda: self.play("sleep"))
        self._timer = QTimer(self); self._timer.timeout.connect(self._next_frame)
        self._animation = None; self._frame = 0
        self.play("idle"); self._last_activity.start(30000)

    def play(self, name: str):
        if name not in self.animations: return
        self._animation, self._frame = self.animations[name], 0
        self._timer.stop(); self._show_frame()
        self._timer.start(round(1000 / self._animation.fps))
        if name != "sleep": self._last_activity.start(30000)

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
        self._last_activity.start(30000)
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._activity(); self._drag_offset = event.globalPosition().toPoint() - self.pos()
    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton: self.move(event.globalPosition().toPoint() - self._drag_offset)
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton: self._drag_offset = None; self.play("wave")
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton: self.play("wave")
    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self); actions = [("手を振る", "wave"), ("考え中", "thinking"), ("眠る", "sleep")]
        for text, name in actions: menu.addAction(text, lambda n=name: self.play(n))
        menu.addSeparator(); menu.addAction("初期位置へ戻す", self.reset_position); menu.addAction("終了", QApplication.instance().quit); menu.exec(event.globalPos())
    def reset_position(self): self.move(100, 100); self._activity()
    def closeEvent(self, event): save_position(self.position_path, self.pos()); super().closeEvent(event)

def main() -> int:
    root = application_root(); (root / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=root / "logs" / "windows_pet.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv)
    try: animations = load_animations(assets_root())
    except RuntimeError as exc:
        logging.exception("起動失敗"); QMessageBox.critical(None, "Windows Pet 起動失敗", str(exc)); return 1
    window = PetWindow(animations, root / "data" / "position.json")
    screen = app.primaryScreen().availableGeometry(); window.move(constrain_to_primary(load_position(window.position_path), screen, window.width())); window.show()
    app.aboutToQuit.connect(lambda: save_position(window.position_path, window.pos()))
    return app.exec()

if __name__ == "__main__": raise SystemExit(main())
