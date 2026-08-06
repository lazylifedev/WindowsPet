from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

CHAT_WIDTH, CHAT_HEIGHT = 380, 460

def chat_position(pet_rect: QRect, available: QRect, size=(CHAT_WIDTH, CHAT_HEIGHT)) -> QPoint:
    width, height = size
    right = pet_rect.right() + 12
    left = pet_rect.left() - width - 12
    if right + width <= available.right() + 1: x = right
    elif left >= available.left(): x = left
    else: x = min(max(right, available.left()), available.right() - width + 1)
    y = min(max(pet_rect.center().y() - height // 2, available.top()), available.bottom() - height + 1)
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
        super().__init__(); self.pet = pet; self._pending = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool); self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(320, 300); self.resize(CHAT_WIDTH, CHAT_HEIGHT)
        card = QFrame(self); card.setObjectName("card"); card.setStyleSheet("QFrame#card { background:#fff; border:1px solid #d8dee8; border-radius:18px; }")
        shadow = QGraphicsDropShadowEffect(card); shadow.setBlurRadius(22); shadow.setOffset(0, 5); shadow.setColor(QColor(0, 0, 0, 45)); card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self); outer.setContentsMargins(10, 10, 10, 10); outer.addWidget(card)
        layout = QVBoxLayout(card); layout.setContentsMargins(16, 14, 16, 14); layout.setSpacing(10)
        header = QHBoxLayout(); title = QLabel("Windows Pet"); title.setFont(QFont("Segoe UI", 11, QFont.Bold)); title.setStyleSheet("color:#172033")
        close = QPushButton("×"); close.setFixedSize(30, 30); close.clicked.connect(self.close); header.addWidget(title); header.addStretch(); header.addWidget(close); layout.addLayout(header)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame)
        self.messages = QWidget(); self.message_layout = QVBoxLayout(self.messages); self.message_layout.setAlignment(Qt.AlignTop); self.message_layout.setSpacing(8); self.scroll.setWidget(self.messages); layout.addWidget(self.scroll, 1)
        self._add_message("こんにちは。何かお手伝いできますか？", False)
        self.input = MessageEdit(); self.input.setPlaceholderText("メッセージを入力してください"); self.input.setFixedHeight(76); self.input.submit.connect(self.send_message)
        self.send_button = QPushButton("送信"); self.send_button.setFixedWidth(70); self.send_button.clicked.connect(self.send_message)
        row = QHBoxLayout(); row.addWidget(self.input, 1); row.addWidget(self.send_button, 0, Qt.AlignBottom); layout.addLayout(row)
        self.reply_timer = QTimer(self); self.reply_timer.setSingleShot(True); self.reply_timer.timeout.connect(self._show_reply)
    @property
    def pending(self): return self._pending
    def _add_message(self, text, user):
        row = QHBoxLayout(); bubble = QLabel(text); bubble.setWordWrap(True); bubble.setTextInteractionFlags(Qt.TextSelectableByMouse); bubble.setMaximumWidth(285); bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); bubble.setStyleSheet(f"QLabel {{ padding:9px 12px; border-radius:14px; color:#18202a; background:{'#dbeafe' if user else '#f1f5f9'}; }}")
        if user: row.addStretch(); row.addWidget(bubble)
        else: row.addWidget(bubble); row.addStretch()
        self.message_layout.addLayout(row); QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
    def send_message(self):
        if self._pending: return False
        text = self.input.toPlainText().strip()
        if not text: return False
        self.input.clear(); self._add_message(text, True); self._pending = True; self.send_button.setEnabled(False); self.send_started.emit(); self.pet.play("thinking"); self.reply_timer.start(700); return True
    def _show_reply(self):
        if not self.isVisible(): self._pending = False; self.send_button.setEnabled(True); self.send_finished.emit(); return
        self._add_message("まだAIには接続されていません。\n次の段階でChatGPT APIとWindows操作機能を追加します。", False); self._pending = False; self.send_button.setEnabled(True); self.send_finished.emit(); self.pet.play("idle")
    def showEvent(self, event): super().showEvent(event); self.input.setFocus()
    def closeEvent(self, event): self.closed.emit(); super().closeEvent(event)
