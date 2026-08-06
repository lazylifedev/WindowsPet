from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from .ai_worker import AIWorker
from .conversation import Conversation

CHAT_WIDTH, CHAT_HEIGHT = 400, 460
CHAT_MIN_WIDTH, CHAT_MIN_HEIGHT = 340, 360
TAIL_WIDTH, TAIL_HEIGHT = 14, 24

COLORS = {
    "card": "#ffffff", "conversation": "#f3f5f8", "text": "#202733",
    "border": "#d8dee8", "assistant": "#ffffff", "user": "#dceeff",
}


def chat_position(pet_rect: QRect, available: QRect, size=(CHAT_WIDTH, CHAT_HEIGHT)) -> QPoint:
    width, height = size
    right = pet_rect.right() + 12
    left = pet_rect.left() - width - 12
    x = right if right + width <= available.right() + 1 else left if left >= available.left() else min(max(right, available.left()), available.right() - width + 1)
    y = min(max(pet_rect.center().y() - height // 2, available.top()), max(available.top(), available.bottom() - height + 1))
    return QPoint(x, y)


class MessageEdit(QTextEdit):
    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submit.emit(); event.accept(); return
        super().keyPressEvent(event)


class ChatBubble(QWidget):
    closed = Signal(); send_started = Signal(); send_finished = Signal()

    def __init__(self, pet):
        super().__init__()
        self.pet = pet; self._pending = False; self.conversation = Conversation()
        self._thread = None; self._worker = None; self._reply_bubble = None; self._reply_text = ""
        self._tail_left = True
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(CHAT_MIN_WIDTH, CHAT_MIN_HEIGHT); self.resize(CHAT_WIDTH, CHAT_HEIGHT)

        self.card = QFrame(self); self.card.setObjectName("card")
        self.card.setStyleSheet(f"QFrame#card {{ background:{COLORS['card']}; border:1px solid {COLORS['border']}; border-radius:18px; }}")
        shadow = QGraphicsDropShadowEffect(self.card); shadow.setBlurRadius(22); shadow.setOffset(0, 5); shadow.setColor(QColor(0, 0, 0, 45)); self.card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self); outer.setContentsMargins(TAIL_WIDTH, 8, TAIL_WIDTH, 8); outer.addWidget(self.card)
        layout = QVBoxLayout(self.card); layout.setContentsMargins(14, 10, 14, 12); layout.setSpacing(8)

        header = QHBoxLayout(); header.setContentsMargins(0, 0, 0, 0); header.setSpacing(6)
        title = QLabel("Windows Pet"); title.setFont(QFont("Segoe UI", 11, QFont.Bold)); title.setStyleSheet(f"color:{COLORS['text']};")
        clear = QPushButton("消去"); clear.setToolTip("会話を消去"); clear.clicked.connect(self.clear_messages)
        close = QPushButton("×"); close.setToolTip("閉じる"); close.clicked.connect(self.close)
        for button in (clear, close): button.setFixedHeight(30); button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        close.setFixedWidth(30); header.addWidget(title); header.addStretch(); header.addWidget(clear); header.addWidget(close); layout.addLayout(header)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame); self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.scroll.setStyleSheet("QScrollArea { border:0; background:#f3f5f8; }")
        self.messages = QWidget(); self.messages.setStyleSheet(f"background:{COLORS['conversation']};")
        self.message_layout = QVBoxLayout(self.messages); self.message_layout.setContentsMargins(8, 8, 8, 8); self.message_layout.setSpacing(8); self.message_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.messages); layout.addWidget(self.scroll, 1)

        self.input = MessageEdit(); self.input.setPlaceholderText("メッセージを入力してください"); self.input.setMinimumHeight(55); self.input.setMaximumHeight(120); self.input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); self.input.submit.connect(self.send_message)
        self.send_button = QPushButton("送信"); self.send_button.setFixedSize(42, 38); self.send_button.clicked.connect(self.send_message)
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8); row.addWidget(self.input, 1); row.addWidget(self.send_button, 0, Qt.AlignBottom); layout.addLayout(row)
        self._add_message("こんにちは。何かお手伝いできますか？", False)

    @property
    def pending(self): return self._pending

    def clear_messages(self):
        while self.message_layout.count():
            item = self.message_layout.takeAt(0); item.deleteLater()
        self._add_message("こんにちは。何かお手伝いできますか？", False)

    def _add_message(self, text, user):
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        bubble = QLabel(text); bubble.setWordWrap(True); bubble.setTextInteractionFlags(Qt.TextSelectableByMouse); bubble.setMaximumWidth(285); bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        bubble.setStyleSheet(f"QLabel {{ padding:9px 12px; border-radius:14px; color:{COLORS['text']}; background:{COLORS['user'] if user else COLORS['assistant']}; }}")
        if user: row.addStretch()
        row.addWidget(bubble)
        if not user: row.addStretch()
        self.message_layout.addLayout(row); QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
        return bubble

    def paintEvent(self, event):
        super().paintEvent(event); painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        x = 0 if self._tail_left else self.width() - TAIL_WIDTH; y = self.height() // 2 - TAIL_HEIGHT // 2
        path = QPainterPath(); path.moveTo(x + (TAIL_WIDTH if self._tail_left else 0), y); path.lineTo(x + (0 if self._tail_left else TAIL_WIDTH), y + TAIL_HEIGHT // 2); path.lineTo(x + (TAIL_WIDTH if self._tail_left else 0), y + TAIL_HEIGHT); path.closeSubpath()
        painter.fillPath(path, QColor(COLORS["card"])); painter.setPen(QPen(QColor(COLORS["border"]), 1)); painter.drawPath(path)

    def set_tail_left(self, value):
        if self._tail_left != value: self._tail_left = value; self.update()

    def send_message(self):
        if self._pending: return False
        text = self.input.toPlainText().strip()
        if not text: return False
        self.input.clear(); self._add_message(text, True); self.conversation.add_user(text); self._pending = True; self.send_button.setEnabled(False); self.send_started.emit(); self.pet.play("thinking")
        self._reply_text = ""; self._reply_bubble = self._add_message("…", False); self._thread = QThread(self); self._worker = AIWorker(self.conversation.messages()); self._worker.moveToThread(self._thread); self._thread.started.connect(self._worker.run); self._worker.delta.connect(self._on_delta); self._worker.finished.connect(self._on_finished); self._worker.failed.connect(self._on_failed); self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit); self._thread.finished.connect(self._worker.deleteLater); self._thread.finished.connect(self._thread.deleteLater); self._thread.finished.connect(self._thread_done); self._thread.start(); return True

    def _on_delta(self, text): self._reply_text += text; self._reply_bubble.setText(self._reply_text); self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
    def _on_finished(self, text): self._reply_bubble.setText(text); self.conversation.add_assistant(text); self._complete()
    def _on_failed(self, kind, message): self._reply_bubble.setText(message); self._complete()
    def _complete(self): self._pending = False; self.send_button.setEnabled(True); self.send_finished.emit(); self.pet.play("idle")
    def _thread_done(self): self._thread = None; self._worker = None
    def showEvent(self, event): super().showEvent(event); self.input.setFocus()
    def closeEvent(self, event):
        if self._thread and self._thread.isRunning(): self._thread.quit(); self._thread.wait(2000)
        self.closed.emit(); super().closeEvent(event)
