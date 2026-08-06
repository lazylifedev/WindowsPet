import logging
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox, QWidget

from .animation import load_animations
from .chat_bubble import InputBubble, chat_position, response_position
from .paths import application_root, assets_root
from .storage import constrain_to_primary, load_position, save_position
from .file_search_settings_window import FileSearchSettingsWindow
from .search_results_window import SearchResultsWindow
from .search_result_store import SearchResultStore
from .openai_settings_window import OpenAISettingsWindow


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
        self.search_store = SearchResultStore()
        self.search_settings_window = None
        self.search_results_window = None
        self.openai_settings_window = None
        self.input_bubble.search_completed.connect(self._on_search_completed)
        self._pet_hovered = False; self._input_hovered = False; self._input_has_focus = False
        self._draft_exists = False; self._api_request_in_progress = False
        self._input_hide_timer = QTimer(self); self._input_hide_timer.setSingleShot(True); self._input_hide_timer.timeout.connect(self._hide_input_if_allowed)
        self.input_bubble.pointer_entered.connect(lambda: self._set_input_hovered(True))
        self.input_bubble.pointer_left.connect(lambda: self._set_input_hovered(False))
        self.input_bubble.focus_state_changed.connect(self._set_input_focus)
        self.input_bubble.draft_state_changed.connect(lambda v: setattr(self, '_draft_exists', v))
        self.input_bubble.send_started.connect(lambda: setattr(self, '_api_request_in_progress', True))
        self.input_bubble.send_finished.connect(lambda: setattr(self, '_api_request_in_progress', False))
        self.play("idle"); self._last_activity.start(30000)

    def play(self, name: str):
        if name == "sleep" and not self._can_sleep(): return
        if name not in self.animations: return
        self._animation, self._frame = self.animations[name], 0; self._timer.stop(); self._show_frame(); self._timer.start(round(1000 / self._animation.fps))
        if name != "sleep" and not self.input_bubble.isVisible() and not self.input_bubble.pending: self._last_activity.start(30000)

    def _show_frame(self): self.label.setPixmap(self._animation.frames[self._frame].scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def visible_pet_rect(self):
        pixmap = self.label.pixmap()
        if pixmap is None or pixmap.isNull(): return self.frameGeometry()
        image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        left, top, right, bottom = image.width(), image.height(), -1, -1
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).alpha() > 0:
                    left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
        if right < left: return self.frameGeometry()
        return QRect(self.mapToGlobal(QPoint(left, top)), self.mapToGlobal(QPoint(right, bottom))).normalized()
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

    def _can_sleep(self):
        return not (self._pet_hovered or self._input_hovered or self._input_has_focus or self._draft_exists or self._api_request_in_progress)
    def _set_input_hovered(self, value):
        self._input_hovered = value
        if value: self._input_hide_timer.stop()
        else: self.schedule_input_bubble_hide()
    def _set_input_focus(self, value):
        self._input_has_focus = value
        if value: self._input_hide_timer.stop()
        else: self.schedule_input_bubble_hide()
    def should_keep_input_bubble_visible(self):
        return not self._can_sleep()
    def schedule_input_bubble_hide(self):
        if self.input_bubble.isVisible() and not self.should_keep_input_bubble_visible(): self._input_hide_timer.start(500)
    def cancel_input_bubble_hide(self): self._input_hide_timer.stop()
    def _hide_input_if_allowed(self):
        if not self.should_keep_input_bubble_visible(): self._hide_chat_bubble()

    def enterEvent(self, event):
        self._pet_hovered = True; self.cancel_input_bubble_hide(); self._activity(); self._show_chat_bubble(); super().enterEvent(event)
    def leaveEvent(self, event):
        self._pet_hovered = False; self.schedule_input_bubble_hide(); super().leaveEvent(event)
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
        menu = self._build_context_menu()
        menu.exec(event.globalPos())

    def _build_context_menu(self):
        menu = QMenu(self)
        menu.addAction('OpenAI API 設定', self.open_openai_settings)
        menu.addAction('ファイル検索設定', self.open_file_search_settings)
        recent = menu.addAction('最近の検索結果', self.show_recent_search)
        recent.setEnabled(self.search_store.latest() is not None)
        cancel = menu.addAction('検索をキャンセル', self.input_bubble.cancel_search)
        cancel.setEnabled(self.input_bubble.search_in_progress)
        menu.addSeparator()
        menu.addAction('チャットを開く', self.open_chat)
        menu.addAction('チャットを閉じる', self.close_chat)
        menu.addAction('会話履歴', self.input_bubble.show_history)
        menu.addAction('位置をリセット', self.reset_position)
        menu.addAction('終了', QApplication.instance().quit)
        return menu
    def reset_position(self): self.move(100, 100); self._activity()
    def open_file_search_settings(self):
        if self.search_settings_window is None:
            self.search_settings_window = FileSearchSettingsWindow(parent=self)
        self.search_settings_window.show(); self.search_settings_window.raise_(); self.search_settings_window.activateWindow()
    def open_openai_settings(self):
        if self.openai_settings_window is None:
            self.openai_settings_window = OpenAISettingsWindow(self)
        self.openai_settings_window.show(); self.openai_settings_window.raise_(); self.openai_settings_window.activateWindow()
    def show_recent_search(self):
        session = self.search_store.latest()
        if session:
            if self.search_results_window is None or self.search_results_window.session is not session:
                self.search_results_window = SearchResultsWindow(session)
            self.search_results_window.show(); self.search_results_window.raise_(); self.search_results_window.activateWindow()
    def _on_search_completed(self, result):
        session = self.search_store.add(result.get('query', ''), tuple(result.get('root_ids', ())), result)
        if self.search_results_window is None:
            self.search_results_window = SearchResultsWindow(session, self)
        else:
            self.search_results_window.session = session
            self.search_results_window._populate()
        self.search_results_window.show(); self.search_results_window.raise_(); self.search_results_window.activateWindow()
    def closeEvent(self, event): self.input_bubble.close(); save_position(self.position_path, self.pos()); super().closeEvent(event)


def main() -> int:
    root = application_root(); (root / "logs").mkdir(exist_ok=True); logging.basicConfig(filename=root / "logs" / "windows_pet.log", level=logging.INFO, encoding="utf-8")
    logging.info("startup diagnostics frozen=%s meipass=%s executable=%s cwd=%s manifest=%s", getattr(sys, "frozen", False), getattr(sys, "_MEIPASS", None), sys.executable, Path.cwd(), assets_root() / "manifest.json")
    app = QApplication(sys.argv)
    try: animations = load_animations(assets_root()); logging.info("animation assets loaded")
    except RuntimeError as exc: logging.exception("asset loading failed"); QMessageBox.critical(None, "Windows Pet", str(exc)); return 1
    window = PetWindow(animations, root / "data" / "position.json"); screen = app.primaryScreen().availableGeometry(); window.move(constrain_to_primary(load_position(window.position_path), screen, window.width())); window.show(); logging.info("pet window shown"); app.aboutToQuit.connect(window.input_bubble.close); return app.exec()


if __name__ == "__main__": raise SystemExit(main())
