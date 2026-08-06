import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QKeyEvent

from windows_pet.ai_client import AIClient, AIClientError, _error
from windows_pet.chat_bubble import ChatBubble, HistoryWindow, MessageEdit, chat_position, response_position


class Pet:
    def __init__(self): self.plays = []
    def play(self, name): self.plays.append(name)


def make_chat(qapp):
    chat = ChatBubble(Pet()); chat.show(); qapp.processEvents(); return chat


def test_bubbles_and_input_keyboard_contract(qapp):
    chat = make_chat(qapp)
    assert not chat.response.isVisible() and chat.input.isVisible()
    chat.input.setPlainText("draft")
    assert chat.input.toPlainText() == "draft"
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    chat.input.keyPressEvent(event)
    assert chat.pending
    chat.close()


def test_shift_enter_inserts_newline_and_blank_is_rejected(qapp):
    chat = make_chat(qapp)
    chat.input.setPlainText("one")
    chat.input.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ShiftModifier))
    assert "\n" in chat.input.toPlainText()
    chat.input.setPlainText("  \n ")
    assert chat.send_message() is False
    chat.close()


def test_input_grows_but_is_capped(qapp):
    chat = make_chat(qapp)
    chat.input.setPlainText("\n".join(["line"] * 30)); qapp.processEvents()
    assert 48 <= chat.input.height() <= 130
    chat.close()


def test_duplicate_send_is_blocked_and_response_streams(qapp):
    chat = make_chat(qapp)
    chat.input.setPlainText("hello")
    assert chat.send_message() is True and chat.send_message() is False
    chat._on_delta("hel"); chat._on_delta("lo")
    assert chat.response.text() == "hello"
    chat._on_finished("hello there")
    assert not chat.pending and chat.conversation.messages()[-1]["role"] == "assistant"
    chat.close()


def test_clear_is_blocked_during_request_and_history_reflects_conversation(qapp):
    chat = make_chat(qapp); chat.conversation.add_user("question"); chat.conversation.add_assistant("answer")
    chat._pending = True; assert chat.clear_messages() is False; assert chat.conversation.messages()
    chat._pending = False; assert chat.clear_messages() is True; assert not chat.conversation.messages()
    chat.conversation.add_user("new"); chat.conversation.add_assistant("reply")
    history = HistoryWindow(chat.conversation); history.show(); qapp.processEvents()
    assert "new" in [w.text() for w in history.findChildren(type(chat.response))]
    history.close(); chat.close()


def test_response_auto_hide_and_pinned(monkeypatch, qapp):
    timers = []
    monkeypatch.setattr("windows_pet.chat_bubble.QTimer.singleShot", lambda ms, fn: timers.append(fn))
    chat = make_chat(qapp); chat._complete(); assert timers; timers.pop()(); assert not chat.response.isVisible()
    chat.response.show(); chat.set_response_pinned(True); chat._complete(); assert not timers; assert chat.response.isVisible(); chat.close()

def test_input_height_contract_and_reset(qapp):
    chat = make_chat(qapp)
    assert 48 <= chat.input.height() <= 130
    chat.input.setPlainText("\n".join(["line"] * 8)); qapp.processEvents()
    grown = chat.input.height()
    chat.input.setPlainText("\n".join(["line"] * 40)); qapp.processEvents()
    assert grown > 48 and chat.input.height() <= 130
    chat.input.clear(); qapp.processEvents(); assert 48 <= chat.input.height() <= 50
    chat.close()

def test_response_empty_and_simultaneous_bubbles_are_safe(qapp):
    chat = make_chat(qapp)
    chat.response_bubble.setText("answer"); chat._position_response(); chat.response_bubble.show(); qapp.processEvents()
    assert chat.response_bubble.y() + chat.response_bubble.height() <= qapp.primaryScreen().availableGeometry().bottom() + 1
    chat._on_finished("")
    assert not chat.response_bubble.isVisible()
    chat.close()


def test_position_spec_edges_and_api_error_mapping(monkeypatch):
    screen = QRect(0, 0, 1000, 800)
    center = chat_position(QRect(400, 300, 100, 100), screen, (280, 100))
    assert center.y() > 400 and center.x() == 309
    top = chat_position(QRect(400, 0, 100, 100), screen, (280, 100))
    assert top.y() == 108
    bottom = chat_position(QRect(400, 750, 100, 50), screen, (280, 100))
    assert bottom.y() < 750 and bottom.y() >= 0
    assert chat_position(QRect(0, 300, 100, 100), screen, (280, 100)).x() == 0
    assert chat_position(QRect(900, 300, 100, 100), screen, (280, 100)).x() == 720
    response = response_position(QRect(400, 300, 100, 100), screen, (280, 100))
    assert response.y() < 300
    assert response_position(QRect(400, 0, 100, 100), screen, (280, 100)).y() > 100
    assert _error(RuntimeError("401 unauthorized")).kind == "auth"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try: AIClient()
    except AIClientError as exc: assert exc.kind == "missing_key"
    else: assert False


def test_pyinstaller_spec_includes_package_modules():
    spec = Path("WindowsPet.spec").read_text(encoding="utf-8")
    assert "collect_submodules('windows_pet')" in spec and "chat_bubble.py" not in spec
