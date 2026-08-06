from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QApplication)

from .ai_worker import AIWorker
from .conversation import Conversation

MIN_INPUT_HEIGHT, MAX_INPUT_HEIGHT = 52, 140
TAIL_WIDTH, TAIL_HEIGHT = 14, 18
SHADOW_BLUR, SHADOW_OFFSET_Y = 12, 3
RESPONSE_GAP = 3

def chat_position(pet_rect: QRect, available: QRect, size=(280, MIN_INPUT_HEIGHT)) -> QPoint:
    width, height = size
    gap = 8
    max_x, max_y = available.right() - width + 1, available.bottom() - height + 1
    centered_x = pet_rect.center().x() - width // 2
    candidates = (
        (min(max(centered_x, available.left()), max_x), pet_rect.bottom() + 1 + gap),
        (pet_rect.right() + 1 + gap, pet_rect.bottom() - height + 1),
        (pet_rect.left() - width - gap, pet_rect.bottom() - height + 1),
        (min(max(centered_x, available.left()), max_x), pet_rect.top() - height - gap),
    )
    for x, y in candidates:
        if (available.left() <= x <= max_x and available.top() <= y <= max_y):
            return QPoint(x, y)
    # If no preferred slot fits, retain the closest possible placement while
    # keeping the complete translucent/shadow outer rect on-screen.
    return QPoint(min(max(centered_x, available.left()), max_x),
                  min(max(pet_rect.bottom() + 1 + gap, available.top()), max_y))

def response_position(pet_rect: QRect, available: QRect, size) -> QPoint:
    width, height = (size.width(), size.height()) if hasattr(size, "width") else size
    x = pet_rect.center().x() - width // 2
    y = pet_rect.top() - height - RESPONSE_GAP
    if y < available.top():
        y = pet_rect.bottom() + 1 + RESPONSE_GAP
    return QPoint(min(max(x, available.left()), available.right() - width + 1),
                  min(max(y, available.top()), available.bottom() - height + 1))

class MessageEdit(QPlainTextEdit):
    submit = Signal()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submit.emit(); event.accept(); return
        super().keyPressEvent(event)

class BubbleFrame(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tail_direction = "top"
        self._tail_x = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    def set_tail_direction(self, direction):
        if direction not in ("top", "bottom"):
            raise ValueError(f"unsupported bubble tail direction: {direction}")
        self._tail_direction = direction
        self.update()

    def set_tail_top(self, value):
        """Compatibility wrapper with an explicit, readable boolean meaning."""
        self.set_tail_direction("top" if value else "bottom")

    def set_tail_x(self, x):
        self._tail_x = max(9, min(self.width() - 9, int(x))) if self.width() else int(x)
        self.update()

    def tail_tip_local_y(self):
        return 0 if self._tail_direction == "top" else self.height()

    def panel_rect_local(self):
        top = TAIL_HEIGHT if self._tail_direction == "top" else 0
        bottom = TAIL_HEIGHT if self._tail_direction == "bottom" else 0
        return QRect(0, top, self.width(), max(0, self.height() - top - bottom))

    def panel_bottom_local_y(self):
        return self.panel_rect_local().top() + self.panel_rect_local().height()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        top_margin = TAIL_HEIGHT if self._tail_direction == "top" else 0
        bottom_margin = TAIL_HEIGHT if self._tail_direction == "bottom" else 0
        card = self.panel_rect_local()
        path = QPainterPath(); path.addRoundedRect(card, 14, 14)
        p.fillPath(path, QColor('#20242b')); p.setPen(QPen(QColor('#4d5663'), 1)); p.drawPath(path)
        x = self._tail_x if self._tail_x is not None else self.width() // 2
        tail = QPainterPath()
        if self._tail_direction == "top":
            tail.moveTo(x-9, top_margin+2); tail.lineTo(x, 0); tail.lineTo(x+9, top_margin+2)
        else:
            tail.moveTo(x-9, self.height()-bottom_margin-2); tail.lineTo(x, self.height()); tail.lineTo(x+9, self.height()-bottom_margin-2)
        tail.closeSubpath(); p.fillPath(tail, QColor('#20242b'))

class ResponseBubble(BubbleFrame):
    def __init__(self, parent=None):
        super().__init__(parent); self.label = QLabel(self); self.label.setWordWrap(True); self.label.setStyleSheet('color:white; padding:10px 14px;'); self._text=''
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
    def setText(self, text):
        self._text=text; self.label.setText(text); self.label.adjustSize(); self.resize(min(380, max(90, self.label.sizeHint().width()+28)), min(180, max(44, self.label.sizeHint().height()+TAIL_HEIGHT+8))); self._layout_label()
    def set_tail_direction(self, direction):
        super().set_tail_direction(direction)
        self._layout_label()
    def _layout_label(self):
        margin = TAIL_HEIGHT if self._tail_direction == "top" else 0
        self.label.setGeometry(0, margin, self.width(), max(0, self.height() - margin - (TAIL_HEIGHT if self._tail_direction == "bottom" else 0)))

class HistoryWindow(QDialog):
    def __init__(self, conversation, parent=None):
        super().__init__(parent); self.setWindowTitle('会話履歴'); self.resize(680, 560)
        area=QScrollArea(); area.setWidgetResizable(True); body=QWidget(); layout=QVBoxLayout(body)
        for message in conversation.messages():
            label=QLabel(message['content']); label.setWordWrap(True); layout.addWidget(label)
        layout.addStretch(); area.setWidget(body); QVBoxLayout(self).addWidget(area)

class InputBubble(BubbleFrame):
    pointer_entered = Signal()
    pointer_left = Signal()
    focus_state_changed = Signal(bool)
    draft_state_changed = Signal(bool)
    closed=Signal(); send_started=Signal(); send_finished=Signal(); search_started=Signal(); search_completed=Signal(dict)
    def __init__(self, pet, worker_factory=AIWorker):
        super().__init__(); self.pet=pet; self._worker_factory=worker_factory; self._pending=False; self._search_in_progress=False; self.conversation=Conversation(); self._thread=None; self._worker=None; self.response_pinned=False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.card=QFrame(self); self.card.setStyleSheet('QFrame{background:#20242b;border:0;}')
        # A layered top-level window must never receive an effect whose expanded
        # repaint area is outside its own bitmap.  Keep the effect on the child
        # panel and reserve the complete blur/offset area in the parent layout.
        shadow=QGraphicsDropShadowEffect(self.card); shadow.setBlurRadius(SHADOW_BLUR); shadow.setOffset(0, SHADOW_OFFSET_Y); shadow.setColor(QColor(0,0,0,70)); self.card.setGraphicsEffect(shadow)
        outer=QVBoxLayout(self); outer.setContentsMargins(SHADOW_BLUR, TAIL_HEIGHT+SHADOW_BLUR, SHADOW_BLUR, SHADOW_BLUR+SHADOW_OFFSET_Y); outer.setSpacing(0); outer.addWidget(self.card)
        row=QHBoxLayout(self.card); row.setContentsMargins(8,5,8,5); row.setSpacing(6)
        self.input=MessageEdit(); self.input.setPlaceholderText('メッセージを入力してください'); self.input.setMinimumHeight(MIN_INPUT_HEIGHT-10); self.input.setMaximumHeight(MAX_INPUT_HEIGHT); self.input.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed); self.input.setStyleSheet('QPlainTextEdit{color:white;background:#303641;border:1px solid #596473;border-radius:8px;padding:5px;}')
        self.send_button=QPushButton('➤'); self.send_button.setFixedSize(34,34); self.send_button.setStyleSheet('QPushButton{color:white;background:#4d78b8;border:0;border-radius:8px;}')
        row.addWidget(self.input); row.addWidget(self.send_button,0,Qt.AlignBottom); self.input.textChanged.connect(self._adjust_input_height); self.input.submit.connect(self.send_message); self.send_button.clicked.connect(self.send_message); self._adjust_input_height()
        self.response=QLabel(''); self.response.hide()
        self.response_bubble=ResponseBubble(); self.response_bubble.hide(); self._reply_text=''
        self.input.textChanged.connect(lambda: self.draft_state_changed.emit(bool(self.input.toPlainText().strip())))
        self.input.installEventFilter(self)
        self.hide()
    def enterEvent(self, event):
        self.pointer_entered.emit(); super().enterEvent(event)
    def leaveEvent(self, event):
        self.pointer_left.emit(); super().leaveEvent(event)
    def eventFilter(self, watched, qt_event):
        event_type = qt_event.type()
        if watched is self.input:
            if event_type == QEvent.Type.FocusIn:
                self.focus_state_changed.emit(True)
            elif event_type == QEvent.Type.FocusOut:
                self.focus_state_changed.emit(False)
        return super().eventFilter(watched, qt_event)
    @property
    def pending(self): return self._pending
    @property
    def search_in_progress(self): return self._search_in_progress
    def cancel_search(self):
        worker = self._worker
        if worker is not None and hasattr(worker, 'cancel'):
            worker.cancel()
            return True
        return False
    def show_history(self): self.history_window=HistoryWindow(self.conversation); self.history_window.show()
    def clear_messages(self):
        if self._pending:return False
        self.conversation.clear(); return True
    def set_response_pinned(self,pinned): self.response_pinned=pinned
    def _adjust_input_height(self):
        doc_h=self.input.document().documentLayout().documentSize().height()
        line_h=self.input.fontMetrics().lineSpacing()
        doc_h=max(doc_h, self.input.document().blockCount() * line_h)
        height=max(48,min(MAX_INPUT_HEIGHT-10,int(doc_h+14))); self.input.setFixedHeight(height); self.adjustSize()
        if self.isVisible() and hasattr(self.pet, "reposition_input_bubble"):
            self.pet.reposition_input_bubble()
    def send_message(self):
        if self._pending:return False
        text=self.input.toPlainText().strip()
        if not text:return False
        self._reply_text=''
        self.input.clear(); self.conversation.add_user(text); self._pending=True; self.send_button.setEnabled(False); self.send_started.emit(); self.pet.play('thinking'); self.response_bubble.setText('考え中…'); self._position_response(); self.response_bubble.show()
        self._thread=QThread(self); self._worker=self._worker_factory(self.conversation.messages()); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run); self._worker.delta.connect(self._on_delta); self._worker.search_started.connect(self._on_search_started); self._worker.search_completed.connect(self._on_search_completed)
        self._worker.finished.connect(self._on_finished); self._worker.failed.connect(self._on_failed); self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit); self._thread.finished.connect(self._thread_done); self._thread.start(); return True
    def _position_response(self):
        if not hasattr(self.pet, 'frameGeometry'): return
        screen=self.pet.screen() if hasattr(self.pet, 'screen') else None; area=(screen or QApplication.primaryScreen()).availableGeometry(); r=self.response_bubble
        pet_rect = self.pet.visible_pet_rect() if hasattr(self.pet, "visible_pet_rect") else self.pet.frameGeometry()
        x = pet_rect.center().x() - r.width() // 2
        y = pet_rect.top() - r.panel_bottom_local_y() - RESPONSE_GAP
        if y < area.top():
            r.set_tail_direction("top")
            y = pet_rect.bottom() + RESPONSE_GAP
        else:
            r.set_tail_direction("bottom")
            y = pet_rect.top() - r.panel_bottom_local_y() - RESPONSE_GAP
        position = QPoint(x, y)
        position.setX(min(max(position.x(), area.left()), area.right() - r.width() + 1))
        position.setY(min(max(position.y(), area.top()), area.bottom() - r.height() + 1))
        r.set_tail_x(pet_rect.center().x() - position.x())
        r.move(position)
    def _on_search_started(self): self._search_in_progress=True; self.search_started.emit()
    def _on_search_completed(self, result): self._search_in_progress=False; self.search_completed.emit(result)
    def _on_delta(self,text): self._reply_text+=text; self.response.setText(self._reply_text); self.response_bubble.setText(self._reply_text); self._position_response()
    def _on_finished(self,text):
        self.response.setText(text); self.response_bubble.setText(text); self._position_response(); self.conversation.add_assistant(text)
        if not text.strip(): self.response_bubble.hide()
        self._complete()
    def _on_failed(self,kind,message): self.response.setText(message); self.response_bubble.setText(message); self._position_response(); self._complete()
    def _complete(self):
        self._pending=False; self._search_in_progress=False; self.send_button.setEnabled(True); self.send_finished.emit(); self.pet.play('idle')
        if not self.response_pinned: QTimer.singleShot(12000,self._auto_hide_response)
    def _auto_hide_response(self):
        if not self._pending and not self.response_pinned:self.response_bubble.hide()
    def _thread_done(self): self._thread=None; self._worker=None
    def closeEvent(self,event):
        worker, thread = self._worker, self._thread
        if worker is not None and hasattr(worker, "cancel"): worker.cancel()
        if thread and thread.isRunning(): thread.quit(); thread.wait(2000)
        if thread and not thread.isRunning(): self._thread, self._worker = None, None
        self.response_bubble.close(); self.closed.emit(); super().closeEvent(event)

ChatBubble = InputBubble
