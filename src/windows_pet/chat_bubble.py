from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QApplication)

from .ai_worker import AIWorker
from .conversation import Conversation

MIN_INPUT_HEIGHT, MAX_INPUT_HEIGHT = 52, 140
TAIL_WIDTH, TAIL_HEIGHT = 14, 18

def chat_position(pet_rect: QRect, available: QRect, size=(280, MIN_INPUT_HEIGHT)) -> QPoint:
    width, height = size
    # Legacy callers that explicitly request a large chat rectangle retain the
    # old side-placement contract; the real compact bubble uses the default.
    if size != (280, MIN_INPUT_HEIGHT) and (width > 320 or height > 200):
        right, left = pet_rect.right() + 12, pet_rect.left() - width - 12
        x = right if right + width <= available.right() + 1 else left if left >= available.left() else min(max(right, available.left()), available.right() - width + 1)
        y = min(max(pet_rect.center().y() - height // 2, available.top()), max(available.top(), available.bottom() - height + 1))
        return QPoint(x, y)
    gap = 8
    x = pet_rect.center().x() - width // 2
    y = pet_rect.bottom() + 1 + gap
    if y + height > available.bottom() + 1:
        y = pet_rect.top() - height - gap
    x = min(max(x, available.left()), available.right() - width + 1)
    y = min(max(y, available.top()), available.bottom() - height + 1)
    return QPoint(x, y)

class MessageEdit(QPlainTextEdit):
    submit = Signal()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submit.emit(); event.accept(); return
        super().keyPressEvent(event)

class BubbleFrame(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tail_top = True
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    def set_tail_top(self, value): self._tail_top = value; self.update()
    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        margin = TAIL_HEIGHT if self._tail_top else 0
        card = QRect(0, margin, self.width(), self.height() - margin)
        path = QPainterPath(); path.addRoundedRect(card, 14, 14)
        p.fillPath(path, QColor('#20242b')); p.setPen(QPen(QColor('#4d5663'), 1)); p.drawPath(path)
        if self._tail_top:
            x = self.width() // 2
            tail = QPainterPath(); tail.moveTo(x-9, margin+2); tail.lineTo(x, 0); tail.lineTo(x+9, margin+2); tail.closeSubpath()
            p.fillPath(tail, QColor('#20242b'))

class ResponseBubble(BubbleFrame):
    def __init__(self, parent=None):
        super().__init__(parent); self.label = QLabel(self); self.label.setWordWrap(True); self.label.setStyleSheet('color:white; padding:10px 14px;'); self._text=''
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
    def setText(self, text):
        self._text=text; self.label.setText(text); self.label.adjustSize(); self.resize(min(380, max(90, self.label.sizeHint().width()+28)), min(180, max(44, self.label.sizeHint().height()+TAIL_HEIGHT+8))); self.label.setGeometry(0, TAIL_HEIGHT, self.width(), self.height()-TAIL_HEIGHT)

class HistoryWindow(QDialog):
    def __init__(self, conversation, parent=None):
        super().__init__(parent); self.setWindowTitle('Conversation history'); self.resize(680, 560)
        area=QScrollArea(); area.setWidgetResizable(True); body=QWidget(); layout=QVBoxLayout(body)
        for message in conversation.messages():
            label=QLabel(message['content']); label.setWordWrap(True); layout.addWidget(label)
        layout.addStretch(); area.setWidget(body); QVBoxLayout(self).addWidget(area)

class InputBubble(BubbleFrame):
    closed=Signal(); send_started=Signal(); send_finished=Signal()
    def __init__(self, pet):
        super().__init__(); self.pet=pet; self._pending=False; self.conversation=Conversation(); self._thread=None; self._worker=None; self.response_pinned=False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.card=QFrame(self); self.card.setStyleSheet('QFrame{background:#20242b;border:0;}')
        shadow=QGraphicsDropShadowEffect(self); shadow.setBlurRadius(16); shadow.setOffset(0,3); shadow.setColor(QColor(0,0,0,70)); self.setGraphicsEffect(shadow)
        outer=QVBoxLayout(self); outer.setContentsMargins(8, TAIL_HEIGHT+2, 8, 6); outer.setSpacing(0); outer.addWidget(self.card)
        row=QHBoxLayout(self.card); row.setContentsMargins(8,5,8,5); row.setSpacing(6)
        self.input=MessageEdit(); self.input.setPlaceholderText('Type a message...'); self.input.setMinimumHeight(MIN_INPUT_HEIGHT-10); self.input.setMaximumHeight(MAX_INPUT_HEIGHT); self.input.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed); self.input.setStyleSheet('QPlainTextEdit{color:white;background:#303641;border:1px solid #596473;border-radius:8px;padding:5px;}')
        self.send_button=QPushButton('➤'); self.send_button.setFixedSize(34,34); self.send_button.setStyleSheet('QPushButton{color:white;background:#4d78b8;border:0;border-radius:8px;}')
        row.addWidget(self.input); row.addWidget(self.send_button,0,Qt.AlignBottom); self.input.textChanged.connect(self._adjust_input_height); self.input.submit.connect(self.send_message); self.send_button.clicked.connect(self.send_message); self._adjust_input_height()
        self.response=QLabel(''); self.response.hide()
        self.response_bubble=ResponseBubble(); self.response_bubble.hide(); self._reply_text=''
    @property
    def pending(self): return self._pending
    def show_history(self): self.history_window=HistoryWindow(self.conversation); self.history_window.show()
    def clear_messages(self):
        if self._pending:return False
        self.conversation.clear(); return True
    def set_response_pinned(self,pinned): self.response_pinned=pinned
    def _adjust_input_height(self):
        doc_h=self.input.document().documentLayout().documentSize().height(); height=max(48,min(MAX_INPUT_HEIGHT-10,int(doc_h+14))); self.input.setFixedHeight(height); self.adjustSize()
    def send_message(self):
        if self._pending:return False
        text=self.input.toPlainText().strip()
        if not text:return False
        self.input.clear(); self.conversation.add_user(text); self._pending=True; self.send_button.setEnabled(False); self.send_started.emit(); self.pet.play('thinking'); self.response_bubble.setText('考え中…'); self._position_response(); self.response_bubble.show(); self._thread=QThread(self); self._worker=AIWorker(self.conversation.messages()); self._worker.moveToThread(self._thread); self._thread.started.connect(self._worker.run); self._worker.delta.connect(self._on_delta); self._worker.finished.connect(self._on_finished); self._worker.failed.connect(self._on_failed); self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit); self._thread.finished.connect(self._thread_done); self._thread.start(); return True
    def _position_response(self):
        if not hasattr(self.pet, 'frameGeometry'): return
        screen=self.pet.screen() if hasattr(self.pet, 'screen') else None; area=(screen or QApplication.primaryScreen()).availableGeometry(); r=self.response_bubble
        x=self.pet.frameGeometry().center().x()-r.width()//2; y=self.pet.frameGeometry().top()-r.height()-8
        r.move(min(max(x,area.left()),area.right()-r.width()+1),max(area.top(),y))
    def _on_delta(self,text): self._reply_text+=text; self.response.setText(self._reply_text); self.response_bubble.setText(self._reply_text)
    def _on_finished(self,text): self.response.setText(text); self.response_bubble.setText(text); self.conversation.add_assistant(text); self._complete()
    def _on_failed(self,kind,message): self.response.setText(message); self.response_bubble.setText(message); self._complete()
    def _complete(self):
        self._pending=False; self.send_button.setEnabled(True); self.send_finished.emit(); self.pet.play('idle')
        if not self.response_pinned: QTimer.singleShot(12000,self._auto_hide_response)
    def _auto_hide_response(self):
        if not self._pending and not self.response_pinned:self.response_bubble.hide()
    def _thread_done(self): self._thread=None; self._worker=None
    def closeEvent(self,event):
        if self._thread and self._thread.isRunning(): self._thread.quit(); self._thread.wait(2000)
        self.response_bubble.close(); self.closed.emit(); super().closeEvent(event)

ChatBubble = InputBubble
