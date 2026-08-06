from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
    QDialog, QMessageBox)

from .ai_worker import AIWorker
from .conversation import Conversation

CHAT_WIDTH, CHAT_HEIGHT = 360, 285
CHAT_MIN_WIDTH, CHAT_MIN_HEIGHT = 220, 180
TAIL_WIDTH, TAIL_HEIGHT = 14, 24
COLORS = {"card":"#fffdf8", "text":"#292d35", "border":"#d7cfc2", "assistant":"#fffdf8", "user":"#e5f2ff"}

def chat_position(pet_rect: QRect, available: QRect, size=(CHAT_WIDTH, CHAT_HEIGHT)) -> QPoint:
    width, height = size
    right, left = pet_rect.right() + 12, pet_rect.left() - width - 12
    x = right if right + width <= available.right() + 1 else left if left >= available.left() else min(max(right, available.left()), available.right() - width + 1)
    y = min(max(pet_rect.center().y() - height // 2, available.top()), max(available.top(), available.bottom() - height + 1))
    return QPoint(x, y)

class MessageEdit(QTextEdit):
    submit = Signal()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submit.emit(); event.accept(); return
        super().keyPressEvent(event)

class HistoryWindow(QDialog):
    def __init__(self, conversation, parent=None):
        super().__init__(parent); self.setWindowTitle("Conversation history"); self.resize(680, 560)
        area = QScrollArea(); area.setWidgetResizable(True); body = QWidget(); layout = QVBoxLayout(body)
        for message in conversation.messages():
            label = QLabel(message["content"]); label.setWordWrap(True); label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setStyleSheet("padding:10px; background:#e5f2ff; border-radius:10px;" if message["role"] == "user" else "padding:10px; background:#f3f1ed; border-radius:10px;")
            layout.addWidget(label, 0, Qt.AlignRight if message["role"] == "user" else Qt.AlignLeft)
        layout.addStretch(); area.setWidget(body); outer = QVBoxLayout(self); outer.addWidget(area)

class ChatBubble(QWidget):
    closed = Signal(); send_started = Signal(); send_finished = Signal()
    def __init__(self, pet):
        super().__init__(); self.pet = pet; self._pending = False; self.conversation = Conversation(); self._thread = None; self._worker = None; self._reply_bubble = None; self._reply_text = ""; self._tail_left = True; self.history_window = None; self.response_pinned = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool); self.setAttribute(Qt.WA_TranslucentBackground); self.setMinimumSize(CHAT_MIN_WIDTH, CHAT_MIN_HEIGHT); self.resize(CHAT_WIDTH, CHAT_HEIGHT)
        self.card = QFrame(self); self.card.setStyleSheet("QFrame { background:#fffdf8; border:1px solid #d7cfc2; border-radius:18px; }"); shadow = QGraphicsDropShadowEffect(self.card); shadow.setBlurRadius(20); shadow.setOffset(0, 5); shadow.setColor(QColor(0,0,0,45)); self.card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self); outer.setContentsMargins(TAIL_WIDTH, 8, TAIL_WIDTH, 8); outer.addWidget(self.card); layout = QVBoxLayout(self.card); layout.setContentsMargins(14,12,14,12); layout.setSpacing(10)
        title = QLabel(""); title.hide(); close = QPushButton("×"); close.setFixedSize(28,28); close.clicked.connect(self.close); history = QPushButton("History"); history.clicked.connect(self.show_history); head = QHBoxLayout(); head.addWidget(title); head.addStretch(); head.addWidget(history); head.addWidget(close); layout.addLayout(head)
        self.response = QLabel(""); self.response.setWordWrap(True); self.response.setTextInteractionFlags(Qt.TextSelectableByMouse); self.response.setStyleSheet("padding:10px 12px; background:#fffdf8; color:#292d35; border:1px solid #e6ded2; border-radius:14px;"); layout.addWidget(self.response); self.response.hide()
        self.input = MessageEdit(); self.input.setPlaceholderText("Type a message..."); self.input.setMinimumHeight(48); self.input.setMaximumHeight(120); self.input.textChanged.connect(self._adjust_input_height); self.input.submit.connect(self.send_message); self.send_button = QPushButton("➤"); self.send_button.setFixedSize(38,38); self.send_button.clicked.connect(self.send_message); row=QHBoxLayout(); row.addWidget(self.input); row.addWidget(self.send_button,0,Qt.AlignBottom); layout.addLayout(row)
    @property
    def pending(self): return self._pending
    def paintEvent(self, event):
        super().paintEvent(event); p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); x=0 if self._tail_left else self.width()-TAIL_WIDTH; y=self.height()//2-TAIL_HEIGHT//2; path=QPainterPath(); path.moveTo(x+(TAIL_WIDTH if self._tail_left else 0),y); path.lineTo(x+(0 if self._tail_left else TAIL_WIDTH),y+TAIL_HEIGHT//2); path.lineTo(x+(TAIL_WIDTH if self._tail_left else 0),y+TAIL_HEIGHT); path.closeSubpath(); p.fillPath(path,QColor('#fffdf8')); p.setPen(QPen(QColor('#d7cfc2'),1)); p.drawPath(path)
    def set_tail_left(self, value): self._tail_left=value; self.update()
    def show_history(self):
        self.history_window=HistoryWindow(self.conversation, self); self.history_window.show(); self.history_window.raise_()
    def clear_messages(self):
        if self._pending: return False
        self.conversation.clear(); self.response.clear(); self.response.hide(); return True
    def set_response_pinned(self, pinned): self.response_pinned = pinned
    def _adjust_input_height(self):
        height = min(120, max(48, int(self.input.document().size().height()) + 18))
        self.input.setFixedHeight(height); self.adjustSize()
    def send_message(self):
        if self._pending: return False
        text=self.input.toPlainText().strip()
        if not text: return False
        self.input.clear(); self.conversation.add_user(text); self.response.show(); self.response.setText("…"); self._reply_text=""; self._pending=True; self.send_button.setEnabled(False); self.send_started.emit(); self.pet.play('thinking')
        self._thread=QThread(self); self._worker=AIWorker(self.conversation.messages()); self._worker.moveToThread(self._thread); self._thread.started.connect(self._worker.run); self._worker.delta.connect(self._on_delta); self._worker.finished.connect(self._on_finished); self._worker.failed.connect(self._on_failed); self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit); self._thread.finished.connect(self._thread_done); self._thread.start(); return True
    def _on_delta(self,text): self._reply_text+=text; self.response.setText(self._reply_text)
    def _on_finished(self,text): self.response.setText(text); self.conversation.add_assistant(text); self._complete()
    def _on_failed(self,kind,message): self.response.setText(message); self._complete()
    def _complete(self):
        self._pending=False; self.send_button.setEnabled(True); self.send_finished.emit(); self.pet.play('idle')
        if not self.response_pinned: QTimer.singleShot(12000, self._auto_hide_response)
    def _auto_hide_response(self):
        if not self._pending and not self.response_pinned: self.response.hide()
    def _thread_done(self): self._thread=None; self._worker=None
    def showEvent(self,event): super().showEvent(event); self.input.setFocus()
    def closeEvent(self,event):
        if self._thread and self._thread.isRunning(): self._thread.quit(); self._thread.wait(2000)
        self.closed.emit(); super().closeEvent(event)
