from __future__ import annotations

import html
import re

from PySide6.QtCore import QEvent, QPoint, QRect, QThread, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QApplication, QTextBrowser, QMenu, QMessageBox)

from .ai_worker import AIWorker
from .conversation import Conversation
from .openai_credentials import is_api_key_configured

MIN_INPUT_HEIGHT, MAX_INPUT_HEIGHT = 52, 140
TAIL_WIDTH, TAIL_HEIGHT = 14, 18
SHADOW_BLUR, SHADOW_OFFSET_Y = 12, 3
RESPONSE_GAP = 3
CONFIGURATION_ERROR_KINDS = {"missing_key", "auth", "permission", "model"}

# This is deliberately a small Markdown allowlist.  QTextDocument accepts a
# useful subset of Markdown, but it can also interpret HTML and create linked
# resources.  Escape HTML before handing the text to Qt, and remove the only
# Markdown constructs that name a resource or destination.
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\([^\r\n)]*\)")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\([^\r\n)]*\)")
_MARKDOWN_HEADING = re.compile(r"^( {0,3})#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?$")
_MARKDOWN_CSS = """
body { color: white; font-size: 13px; }
h1, h2, h3, h4, h5, h6 { color: white; font-size: 15px; font-weight: bold; margin: 4px 0; }
p { margin: 0 0 5px 0; }
ul, ol { margin: 3px 0 5px 18px; padding: 0; }
pre, code { background: #303641; color: white; font-family: Consolas, monospace; }
pre { margin: 4px 0; white-space: pre-wrap; }
a { color: white; text-decoration: none; }
"""


def sanitize_markdown(text: str) -> str:
    """Return Markdown that cannot interpret HTML or name an external resource."""
    text = "" if text is None else str(text)
    # QTextDocument's Markdown headings use browser-sized fonts.  Preserve a
    # heading's emphasis as normal-sized bold text, except inside fenced code.
    in_fence = False
    normalized_lines = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        if re.match(r"^ {0,3}(`{3,}|~{3,})", body):
            in_fence = not in_fence
            normalized_lines.append(line)
            continue
        match = None if in_fence else _MARKDOWN_HEADING.match(body)
        body = f"{match.group(1)}**{match.group(2)}**" if match else body
        # Markdown treats a lone newline inside a paragraph as a space.  Make
        # it an explicit break so AI answers retain their authored line breaks.
        if not in_fence and ending and body.strip() and not body.endswith("  "):
            body += "  "
        normalized_lines.append(body + ending)
    text = "".join(normalized_lines)
    text = _MARKDOWN_IMAGE.sub(lambda match: match.group(1), text)
    text = _MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    return html.escape(text, quote=False)


class SafeMarkdownBrowser(QTextBrowser):
    """A non-navigable QTextBrowser for untrusted assistant output.

    Sanitizing is the primary defence.  The browser-level restrictions remain
    intentionally redundant so future document changes cannot fetch or open a
    resource accidentally.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fit_height_to_contents = False
        self._resizing_to_contents = False
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setUndoRedoEnabled(False)
        self.document().setDefaultStyleSheet(_MARKDOWN_CSS)

    def loadResource(self, resource_type, name):  # pragma: no cover - Qt calls this only for resources.
        # Do not delegate to QTextBrowser: file, http(s), and every other URL
        # are intentionally unavailable to assistant Markdown.
        return None

    def setSource(self, name):
        # A QTextBrowser normally navigates when an anchor is activated.  Links
        # are stripped, and this makes navigation a no-op even if one appears.
        return None

    def set_plain_text(self, text: str) -> None:
        self.setPlainText("" if text is None else str(text))

    def set_markdown_text(self, text: str) -> None:
        self.document().setMarkdown(sanitize_markdown(text), QTextDocument.MarkdownDialectGitHub)
        self.document().setDefaultStyleSheet(_MARKDOWN_CSS)

    def fit_height_to_contents(self) -> None:
        """Let a history card use its outer scroll area for long messages."""
        self._fit_height_to_contents = True
        QTimer.singleShot(0, self._update_content_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_height_to_contents:
            self._update_content_height()

    def _update_content_height(self) -> None:
        if self._resizing_to_contents or not self._fit_height_to_contents:
            return
        self._resizing_to_contents = True
        try:
            self.document().setTextWidth(max(1, self.viewport().width()))
            self.setFixedHeight(max(24, int(self.document().size().height()) + 2))
        finally:
            self._resizing_to_contents = False

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
        super().__init__(parent)
        self.label = SafeMarkdownBrowser(self)
        self.label.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.label.setStyleSheet('QTextBrowser{color:white; background:transparent; border:0; padding:10px 14px;}')
        self.label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label.customContextMenuRequested.connect(self._show_context_menu)
        self._text=''; self._copy_enabled = False; self._actions_enabled = True
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
    def setText(self, text):
        """Compatibility API: callers that use setText keep plain-text display."""
        self.set_plain_text(text)

    def set_plain_text(self, text):
        self._text = text
        self.label.set_plain_text(text)
        self._resize_for_document()

    def set_markdown_text(self, text):
        """Render a completed assistant response without changing its source text."""
        self._text = text
        self.label.set_markdown_text(text)
        self._resize_for_document()

    def _resize_for_document(self):
        document = self.label.document()
        document.setTextWidth(392)
        natural_width = max(62, min(392, int(document.idealWidth())))
        document.setTextWidth(natural_width)
        content_height = int(document.size().height()) + 20
        panel_height = max(44, min(320, content_height))
        self.resize(natural_width + 28, panel_height + TAIL_HEIGHT)
        self.label.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if content_height > 320 else Qt.ScrollBarAlwaysOff)
        self._layout_label()
    def set_tail_direction(self, direction):
        super().set_tail_direction(direction)
        self._layout_label()
    def _layout_label(self):
        margin = TAIL_HEIGHT if self._tail_direction == "top" else 0
        self.label.setGeometry(0, margin, self.width(), max(0, self.height() - margin - (TAIL_HEIGHT if self._tail_direction == "bottom" else 0)))
    def set_copy_enabled(self, enabled): self._copy_enabled = bool(enabled)
    def set_actions_enabled(self, enabled): self._actions_enabled = bool(enabled)
    def _show_context_menu(self, position):
        self._build_context_menu().exec(self.label.mapToGlobal(position))
    def _build_context_menu(self):
        menu = QMenu(self)
        parent = self.parent()
        if (getattr(parent, '_last_error_kind', None) in CONFIGURATION_ERROR_KINDS
                and not parent.pending and not getattr(parent, 'cancel_requested', False)):
            settings = menu.addAction('OpenAI API設定を開く')
            settings.triggered.connect(parent.request_api_settings)
        if (getattr(parent, '_retry_text', None) and not parent.pending
                and not (parent._thread is not None and parent._thread.isRunning())):
            retry_action = menu.addAction('再試行')
            retry_action.setEnabled(self._actions_enabled and not self._pending_parent())
            retry_action.triggered.connect(parent.retry_last_request)
        copy_action = menu.addAction('回答をコピー')
        copy_action.setEnabled(self._actions_enabled and self._copy_enabled)
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(self.toPlainText()))
        pin_action = menu.addAction('固定を解除' if getattr(parent, 'response_pinned', False) else '回答を固定')
        pin_action.setEnabled(self._actions_enabled and not self._pending_parent())
        pin_action.triggered.connect(self._toggle_pin)
        menu.addSeparator()
        close_action = menu.addAction('閉じる', self._close_response)
        close_action.setEnabled(self._actions_enabled and not self._pending_parent())
        return menu
    def _pending_parent(self):
        parent = self.parent()
        return bool(parent is not None and getattr(parent, 'pending', False))
    def _toggle_pin(self):
        parent = self.parent()
        if parent is not None: parent.set_response_pinned(not parent.response_pinned)
    def _close_response(self):
        parent = self.parent()
        if parent is not None: parent.close_response()
    def toPlainText(self): return self._text

def format_conversation_text(messages) -> str:
    labels = {'user': 'あなた', 'assistant': 'WindowsPet'}
    blocks = []
    for message in messages:
        role = message.get('role', '')
        name = labels.get(role, 'メッセージ')
        blocks.append(f"{name}:\n{message.get('content', '')}")
    return '\n\n'.join(blocks)


class HistoryWindow(QDialog):
    def __init__(self, conversation, parent=None, clear_callback=None):
        super().__init__(parent)
        self.conversation = conversation; self._clear_callback = clear_callback
        self._pending = False
        self.setWindowTitle('会話履歴'); self.resize(680, 560); self.setMinimumSize(420, 320)
        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget(); self.layout = QVBoxLayout(self.body); self.layout.setContentsMargins(14, 14, 14, 14); self.layout.setSpacing(10)
        self.area.setWidget(self.body)
        self.copy_button = QPushButton('会話をコピー'); self.clear_button = QPushButton('履歴を消去'); close_button = QPushButton('閉じる')
        self.copy_button.clicked.connect(self.copy_conversation); self.clear_button.clicked.connect(self.confirm_clear); close_button.clicked.connect(self.close)
        buttons = QHBoxLayout(); buttons.addWidget(self.copy_button); buttons.addWidget(self.clear_button); buttons.addStretch(); buttons.addWidget(close_button)
        root = QVBoxLayout(self); root.addWidget(self.area); root.addLayout(buttons)
        self.refresh()

    def refresh(self, messages=None, pending=None):
        if pending is not None: self._pending = bool(pending)
        while self.layout.count():
            item = self.layout.takeAt(0); widget = item.widget()
            if widget:
                widget.setParent(None); widget.deleteLater()
        messages = self.conversation.messages() if messages is None else messages
        if not messages:
            empty = QLabel('会話履歴はまだありません。'); empty.setAlignment(Qt.AlignCenter); empty.setStyleSheet('color:#aeb7c4; font-size:14px;'); self.layout.addWidget(empty, 1)
        else:
            for message in messages:
                role = message.get('role', '')
                name = {'user': 'あなた', 'assistant': 'WindowsPet'}.get(role, 'メッセージ')
                card = QFrame(); card.setObjectName('message-card'); card.setMaximumWidth(560); card.setStyleSheet('QFrame{background:%s; border-radius:10px; padding:8px;}' % ('#354b68' if role == 'user' else '#2b313b'))
                card_layout = QVBoxLayout(card); title = QLabel(name); title.setStyleSheet('color:#b8c5d6; font-size:11px; font-weight:bold;')
                if role == 'assistant':
                    text = SafeMarkdownBrowser()
                    text.setObjectName('assistant-message')
                    text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                    text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                    text.setStyleSheet('QTextBrowser{color:white; background:transparent; border:0; padding:0;}')
                    text.set_markdown_text(message.get('content', ''))
                    text.fit_height_to_contents()
                else:
                    text = QLabel(); text.setTextFormat(Qt.PlainText); text.setTextInteractionFlags(Qt.TextSelectableByMouse); text.setWordWrap(True); text.setText(message.get('content', '')); text.setMinimumHeight(24); text.setStyleSheet('QLabel{color:white; background:transparent; padding:0;}')
                text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
                card_layout.addWidget(title); card_layout.addWidget(text)
                row_widget = QWidget(); row = QHBoxLayout(row_widget); row.setContentsMargins(0, 0, 0, 0)
                if role == 'user': row.addStretch()
                row.addWidget(card)
                if role != 'user': row.addStretch()
                self.layout.addWidget(row_widget)
            self.layout.addStretch()
        self._update_actions(bool(messages))
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _update_actions(self, has_messages=None):
        has_messages = bool(self.conversation.messages()) if has_messages is None else bool(has_messages)
        self.copy_button.setEnabled(has_messages)
        self.clear_button.setEnabled(has_messages and not self._pending)

    def _scroll_to_bottom(self):
        if self.isVisible():
            bar = self.area.verticalScrollBar(); bar.setValue(bar.maximum())

    def copy_conversation(self):
        if self.copy_button.isEnabled(): QApplication.clipboard().setText(format_conversation_text(self.conversation.messages()))

    def confirm_clear(self):
        if not self.clear_button.isEnabled(): return
        box = QMessageBox(self); box.setWindowTitle('会話履歴を消去'); box.setText('現在の会話履歴をすべて消去しますか？')
        erase = box.addButton('消去', QMessageBox.AcceptRole); box.addButton('キャンセル', QMessageBox.RejectRole); box.exec()
        if box.clickedButton() is erase:
            if self._clear_callback is None or self._clear_callback(): self.refresh()

class InputBubble(BubbleFrame):
    pointer_entered = Signal()
    pointer_left = Signal()
    focus_state_changed = Signal(bool)
    draft_state_changed = Signal(bool)
    closed=Signal(); send_started=Signal(); send_finished=Signal(); search_started=Signal(); search_completed=Signal(dict); api_settings_requested=Signal(); application_launch_requested=Signal(object); application_launch_ready=Signal(object); cancel_processing_requested=Signal()
    def __init__(self, pet, worker_factory=AIWorker):
        super().__init__(); self.pet=pet; self._worker_factory=worker_factory; self._pending=False; self._local_action_pending=False; self._search_in_progress=False; self._search_status_active=False; self.conversation=Conversation(); self._thread=None; self._worker=None; self.response_pinned=False; self._active_user_text=None; self._retry_text=None; self._last_error_kind=None; self._pending_application_launch_request=None
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
        row.addWidget(self.input); row.addWidget(self.send_button,0,Qt.AlignBottom); self.input.textChanged.connect(self._adjust_input_height); self.input.submit.connect(self.send_message); self.send_button.clicked.connect(self._on_primary_button_clicked); self._cancel_requested=False; self._update_primary_button(); self._adjust_input_height()
        self.response=QLabel(''); self.response.hide()
        self.response_bubble=ResponseBubble(self); self.response_bubble.hide(); self._reply_text=''; self._response_generation = 0
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
    def pending(self): return self._pending or self._local_action_pending
    @property
    def search_in_progress(self): return self._search_in_progress
    def request_api_settings(self): self.api_settings_requested.emit()
    @property
    def cancel_requested(self): return self._cancel_requested
    def _update_primary_button(self):
        running = self.pending
        waiting = self._thread is not None and self._thread.isRunning()
        self.send_button.setText('■' if running else '➤')
        self.send_button.setToolTip('処理をキャンセル' if running and not self._cancel_requested else ('キャンセルしています…' if self._cancel_requested else '送信'))
        self.send_button.setEnabled((running and not self._cancel_requested) or (not running and not waiting))
    def _on_primary_button_clicked(self):
        if self.pending:
            if self._cancel_requested:
                return False
            if self._local_action_pending:
                self._cancel_requested = True
                self._pending_application_launch_request = None
                self._show_response_status('キャンセルしています…')
                self._update_primary_button()
            self.cancel_processing_requested.emit()
            return True
        return self.send_message()
    def cancel_current_request(self):
        if not self._pending or self._cancel_requested: return False
        worker = self._worker
        if worker is None or not hasattr(worker, 'cancel'): return False
        worker.cancel(); self._cancel_requested=True
        self._show_response_status('キャンセルしています…'); self._update_primary_button()
        return True
    def cancel_search(self):
        return self.cancel_current_request()
    def show_history(self):
        if not hasattr(self, 'history_window') or self.history_window is None:
            self.history_window = HistoryWindow(self.conversation, clear_callback=self.clear_messages)
        self._refresh_history_window(); self.history_window.show(); self.history_window.raise_(); self.history_window.activateWindow()
    def _refresh_history_window(self, *, refresh_messages=True):
        window = getattr(self, 'history_window', None)
        if window is not None:
            window.refresh(self.conversation.messages() if refresh_messages else None, pending=self.pending)
    def clear_messages(self):
        if self.pending:return False
        self.conversation.clear()
        self._retry_text=None
        self._refresh_history_window()
        return True
    def set_response_pinned(self, pinned):
        pinned = bool(pinned)
        if self.response_pinned == pinned: return
        self.response_pinned = pinned
        if not pinned and not self.pending and self.response_bubble.isVisible():
            self._schedule_response_auto_hide()
    def close_response(self):
        if self.pending: return False
        self.response_pinned = False
        self._retry_text = None
        self._last_error_kind = None
        self._response_generation += 1
        self.response_bubble.hide()
        return True
    def _adjust_input_height(self):
        doc_h=self.input.document().documentLayout().documentSize().height()
        line_h=self.input.fontMetrics().lineSpacing()
        doc_h=max(doc_h, self.input.document().blockCount() * line_h)
        height=max(48,min(MAX_INPUT_HEIGHT-10,int(doc_h+14))); self.input.setFixedHeight(height); self.adjustSize()
        if self.isVisible() and hasattr(self.pet, "reposition_input_bubble"):
            self.pet.reposition_input_bubble()
    def send_message(self):
        if not self._can_start_request():return False
        text=self.input.toPlainText().strip()
        if not text:return False
        if not is_api_key_configured():
            self._last_error_kind = "missing_key"
            self._show_response_status("OpenAI APIキーを設定してください。\n入力内容はそのまま残しています。")
            self.response_bubble.set_copy_enabled(True); self.response_bubble.set_actions_enabled(True); self.response_bubble.show()
            self.api_settings_requested.emit()
            return False
        started = self._start_request(text, clear_input=True)
        if started: self._retry_text=None; self._last_error_kind=None
        return started
    def retry_last_request(self):
        text = self._retry_text
        if not text:return False
        started = self._start_request(text, clear_input=False)
        if started: self._retry_text=None
        return started
    def _can_start_request(self):
        return not self.pending and not (self._thread is not None and self._thread.isRunning())
    def _start_request(self, text, *, clear_input):
        if not text or not self._can_start_request(): return False
        self._last_error_kind=None
        self._cancel_requested=False
        self._reply_text=''; self._search_status_active=False; self.response_pinned=False; self._response_generation += 1
        if clear_input: self.input.clear()
        self._active_user_text=text
        self.conversation.add_user(text); self._pending=True; self.send_button.setEnabled(False); self.send_started.emit(); self.pet.play('thinking'); self.response_bubble.set_plain_text('考え中…'); self._position_response(); self.response_bubble.show()
        self._refresh_history_window()
        self._thread=QThread(self); self._worker=self._worker_factory(self.conversation.messages()); self._worker.moveToThread(self._thread); self._update_primary_button()
        self._thread.started.connect(self._worker.run)
        self._worker.delta.connect(self._on_delta, Qt.ConnectionType.QueuedConnection)
        self._worker.search_started.connect(self._on_search_started, Qt.ConnectionType.QueuedConnection)
        self._worker.search_completed.connect(self._on_search_completed, Qt.ConnectionType.QueuedConnection)
        if hasattr(self._worker, "powershell_started"):
            self._worker.powershell_started.connect(
                self._on_powershell_started, Qt.ConnectionType.QueuedConnection
            )
        if hasattr(self._worker, "powershell_completed"):
            self._worker.powershell_completed.connect(
                self._on_powershell_completed, Qt.ConnectionType.QueuedConnection
            )
        if hasattr(self._worker, "application_launch_requested"):
            self._worker.application_launch_requested.connect(
                self._on_application_launch_requested, Qt.ConnectionType.QueuedConnection
            )
        if hasattr(self._worker, "application_launch_handed_off"):
            self._worker.application_launch_handed_off.connect(
                self._on_local_action_handed_off, Qt.ConnectionType.QueuedConnection
            )
            self._worker.application_launch_handed_off.connect(self._thread.quit)
        self._worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_done, Qt.ConnectionType.QueuedConnection); self._thread.start(); return True
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
    def _show_response_status(self, text: str) -> None:
        self.response.setText(text)
        self.response_bubble.set_plain_text(text)
        self.response_bubble.set_copy_enabled(False); self.response_bubble.set_actions_enabled(False)
        self._position_response()
        if self.pending:
            self.response_bubble.show()

    @Slot()
    def _on_search_started(self):
        self._search_in_progress=True; self._search_status_active=True
        self._show_response_status('ファイルを検索しています…')
        self.search_started.emit()

    @Slot(dict)
    def _on_search_completed(self, result):
        self._search_in_progress=False
        self._show_response_status('検索結果を整理しています…')
        self.search_completed.emit(result)

    @Slot(str)
    def _on_delta(self,text):
        if self._search_status_active:
            self._reply_text=''; self._search_status_active=False
        self._reply_text+=text; self.response.setText(self._reply_text); self.response_bubble.set_plain_text(self._reply_text); self.response_bubble.set_copy_enabled(False); self.response_bubble.set_actions_enabled(False); self._position_response()
    @Slot(str)
    def _on_finished(self,text):
        self._search_status_active=False
        self._retry_text=None
        self._active_user_text=None
        self._last_error_kind=None
        self.response.setText(text); self.response_bubble.set_markdown_text(text); self.response_bubble.set_copy_enabled(bool(text.strip())); self.response_bubble.set_actions_enabled(bool(text.strip())); self._position_response(); self.conversation.add_assistant(text)
        self._refresh_history_window()
        if not text.strip(): self.response_bubble.hide()
        self._complete()
    @Slot(str, str)
    def _on_failed(self,kind,message):
        self._pending_application_launch_request = None
        active=self._active_user_text
        if active is not None and self.conversation.remove_last_user(active): self._retry_text=active
        self._active_user_text=None
        self._last_error_kind=kind
        self.response.setText(message); self.response_bubble.set_plain_text(message); self.response_bubble.set_copy_enabled(bool(message.strip())); self.response_bubble.set_actions_enabled(bool(message.strip())); self._position_response(); self._complete()
        if kind in CONFIGURATION_ERROR_KINDS: QTimer.singleShot(0, self.api_settings_requested.emit)
    def _complete(self):
        self._pending=False; self._local_action_pending=False; self._search_in_progress=False; self._cancel_requested=False; self._update_primary_button(); self.send_finished.emit()
        self._refresh_history_window(refresh_messages=False)
        if not self.response_pinned: self._schedule_response_auto_hide()
    @Slot()
    def _on_local_action_handed_off(self):
        self._pending = False
        self._local_action_pending = True
        self._active_user_text = None
        self._update_primary_button()
        self._refresh_history_window(refresh_messages=False)

    @Slot(object)
    def _on_application_launch_requested(self, request):
        self._pending_application_launch_request = request

    @Slot(str)
    def _on_powershell_started(self, area: str) -> None:
        self._show_response_status('Windowsの状態を調査しています…')

    @Slot(dict)
    def _on_powershell_completed(self, result: dict) -> None:
        self._show_response_status('調査結果を確認しています…')

    def _emit_pending_application_launch_request(self):
        request, self._pending_application_launch_request = self._pending_application_launch_request, None
        if request is not None and self._local_action_pending and not self._cancel_requested:
            self.application_launch_ready.emit(request)

    def show_local_action_status(self, text):
        if not self._local_action_pending:
            return
        self._show_response_status(text)
        self._refresh_history_window(refresh_messages=False)

    def complete_local_action(self, text):
        self._active_user_text=None; self._last_error_kind=None; self.response.setText(text); self.response_bubble.set_plain_text(text); self.response_bubble.set_copy_enabled(True); self.response_bubble.set_actions_enabled(True); self._position_response(); self.response_bubble.show(); self.conversation.add_assistant(text); self._refresh_history_window(); self._complete()
    def _schedule_response_auto_hide(self):
        self._response_generation += 1
        generation = self._response_generation
        QTimer.singleShot(12000, lambda: self._auto_hide_response(generation))
    def _auto_hide_response(self, generation=None):
        if generation is not None and generation != self._response_generation: return
        if not self.pending and not self.response_pinned:self.response_bubble.hide()
    @Slot()
    def _thread_done(self):
        self._thread=None; self._worker=None
        if self._local_action_pending:
            QTimer.singleShot(0, self._emit_pending_application_launch_request)
        if not self.pending:
            self._cancel_requested=False
            self.pet.play('idle')
        self._update_primary_button()
    def closeEvent(self,event):
        worker, thread = self._worker, self._thread
        if worker is not None and hasattr(worker, "cancel"): worker.cancel()
        if thread and thread.isRunning(): thread.quit(); thread.wait(2000)
        if thread and not thread.isRunning(): self._thread, self._worker = None, None
        self._retry_text = None
        self._pending_application_launch_request = None
        self._cancel_requested = False
        self._last_error_kind = None
        if getattr(self, 'history_window', None) is not None: self.history_window.close()
        self.response_bubble.close(); self.closed.emit(); super().closeEvent(event)

ChatBubble = InputBubble
