import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QPointF

from windows_pet.ai_client import AIClient, AIClientError, _error
from windows_pet.chat_bubble import ChatBubble, HistoryWindow, MessageEdit, chat_position, response_position
from windows_pet.main import PetWindow
from windows_pet.animation import load_animations


class Pet:
    def __init__(self): self.plays = []
    def play(self, name): self.plays.append(name)


def make_chat(qapp):
    chat = ChatBubble(Pet()); chat.show(); qapp.processEvents(); return chat


def test_bubbles_and_input_keyboard_contract(qapp):
    chat = make_chat(qapp)
    assert not chat.response.isVisible() and chat.input.isVisible()
    assert chat.input.placeholderText() == "メッセージを入力してください"
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
    assert response.y() == 296 - 100
    assert 300 - (response.y() + 100) == 4
    assert response_position(QRect(400, 0, 100, 100), screen, (280, 100)).y() > 100
    assert _error(RuntimeError("401 unauthorized")).kind == "auth"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try: AIClient()
    except AIClientError as exc: assert exc.kind == "missing_key"
    else: assert False

def test_response_position_is_centered_for_short_and_long_text(qapp):
    chat = make_chat(qapp)
    pet = type("Pet", (), {"frameGeometry": lambda self: QRect(400, 300, 100, 100),
                            "screen": lambda self: qapp.primaryScreen()})()
    chat.pet = pet
    for text in ("考え中…", "こんにちは！今日はどのようなお手伝いをしましょうか？\n詳しく説明します。" * 3):
        chat.response_bubble.setText(text)
        chat._position_response()
        assert chat.response_bubble.x() + chat.response_bubble.width() / 2 == pet.frameGeometry().center().x()
        assert chat.response_bubble.y() + chat.response_bubble.height() == pet.frameGeometry().top() - 4
        assert chat.response_bubble._tail_direction == "bottom"
        assert chat.response_bubble._tail_x == pet.frameGeometry().center().x() - chat.response_bubble.x()
    chat.close()

def test_response_stream_repositions_after_each_resize_and_clamps_edges(qapp):
    chat = make_chat(qapp)
    pet = type("Pet", (), {"frameGeometry": lambda self: self.rect,
                            "screen": lambda self: qapp.primaryScreen()})()
    for rect in (QRect(0, 300, 100, 100), QRect(900, 300, 100, 100)):
        pet.rect = rect; chat.pet = pet; chat.response_bubble.setText("短文"); chat._position_response()
        assert chat.response_bubble.x() >= qapp.primaryScreen().availableGeometry().left()
        assert chat.response_bubble.x() + chat.response_bubble.width() <= qapp.primaryScreen().availableGeometry().right() + 1
        assert chat.response_bubble._tail_x == max(9, min(chat.response_bubble.width() - 9, rect.center().x() - chat.response_bubble.x()))
        old_x = chat.response_bubble.x(); chat._on_delta("長い回答です。" * 30)
        assert chat.response_bubble.x() != old_x or chat.response_bubble.width() >= 90
    chat.close()

def test_input_position_is_centered_below_and_tail_is_clamped(qapp):
    chat = make_chat(qapp)
    pet = type("Pet", (), {"frameGeometry": lambda self: QRect(0, 300, 100, 100),
                            "screen": lambda self: qapp.primaryScreen()})()
    chat.pet = pet
    chat.adjustSize()
    position = chat_position(pet.frameGeometry(), qapp.primaryScreen().availableGeometry(), (chat.width(), chat.height()))
    assert position.y() > pet.frameGeometry().bottom()
    assert position.x() == 0
    chat.set_tail_x(-100)
    assert chat._tail_x == 9
    chat.set_tail_x(10000)
    assert chat._tail_x == chat.width() - 9
    chat.close()


def test_pyinstaller_spec_includes_package_modules():
    spec = Path("WindowsPet.spec").read_text(encoding="utf-8")
    assert "collect_submodules('windows_pet')" in spec and "chat_bubble.py" not in spec


def test_pet_click_opens_and_closes_real_input_bubble_without_legacy_api(qapp, tmp_path):
    animations = load_animations(Path("assets/animations"))
    pet = PetWindow(animations, tmp_path / "position.json")
    pet.move(300, 300)
    pet.show(); qapp.processEvents()
    point = QPointF(30, 30)
    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    pet.mousePressEvent(press); pet.mouseReleaseEvent(release); qapp.processEvents()
    assert pet.input_bubble.isVisible()
    expected_direction = "top" if pet.input_bubble.y() > pet.frameGeometry().bottom() else "bottom"
    assert pet.input_bubble._tail_direction == expected_direction
    pet.mousePressEvent(press); pet.mouseReleaseEvent(release); qapp.processEvents()
    assert not pet.input_bubble.isVisible()
    pet.close()


def test_pet_drag_does_not_toggle_and_edge_bubbles_have_valid_directions(qapp, tmp_path):
    animations = load_animations(Path("assets/animations"))
    pet = PetWindow(animations, tmp_path / "position.json"); pet.move(300, 300); pet.show(); qapp.processEvents()
    pet.input_bubble.hide()
    start, end = QPointF(30, 30), QPointF(100, 30)
    pet.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, start, start, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    pet.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, end, end, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    pet.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, end, end, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert not pet.input_bubble.isVisible()
    pet.move(0, 700); pet.toggle_chat_bubble(); qapp.processEvents()
    assert pet.input_bubble.isVisible() and pet.input_bubble._tail_direction == "bottom"
    pet.input_bubble.response_bubble.setText("reply"); pet.input_bubble._position_response(); pet.input_bubble.response_bubble.show(); qapp.processEvents()
    assert pet.input_bubble.response_bubble._tail_direction == "bottom"
    pet.close()
