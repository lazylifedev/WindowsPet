import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThread, Slot, qInstallMessageHandler
from PySide6.QtGui import QEnterEvent, QFocusEvent, QKeyEvent, QPaintEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QPointF, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QLabel, QSystemTrayIcon

from windows_pet.ai_client import AIClient, AIClientError, _error
from windows_pet.chat_bubble import (ChatBubble, HistoryWindow, MessageEdit, ResponseBubble,
                                     SafeMarkdownBrowser, chat_position, response_position)
from windows_pet.main import PetWindow
from windows_pet.animation import load_animations


class Pet:
    def __init__(self): self.plays = []
    def play(self, name): self.plays.append(name)


class FakeWorker(QObject):
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str, str)
    search_started = Signal()
    search_completed = Signal(dict)

    def __init__(self, history):
        super().__init__(); self.cancelled = False

    def cancel(self): self.cancelled = True

    def run(self): self.finished.emit("fake response")


class PowerShellStatusWorker(QObject):
    """Emits inspection progress only from its assigned worker thread."""
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str, str)
    search_started = Signal()
    search_completed = Signal(dict)
    powershell_started = Signal(str)
    powershell_completed = Signal(dict)

    def __init__(self, history):
        super().__init__()

    @Slot()
    def run(self):
        self.powershell_started.emit("processes")
        self.powershell_completed.emit({"items": []})
        self.finished.emit("確認しました")

    def cancel(self):
        pass


def make_chat(qapp):
    chat = ChatBubble(Pet(), worker_factory=FakeWorker); chat.show(); qapp.processEvents(); return chat


def close_chat(chat, qapp):
    chat.close(); qapp.processEvents()
    assert chat._thread is None and chat._worker is None


def test_powershell_status_signals_update_qtextdocument_on_ui_thread(qapp, qtbot):
    """Queued QObject slots keep QTextDocument changes off the worker QThread."""
    warnings, status_threads, statuses = [], [], []
    previous_handler = qInstallMessageHandler(
        lambda _mode, _context, message: warnings.append(message)
    )
    chat = ChatBubble(Pet(), worker_factory=PowerShellStatusWorker)
    original_show_status = chat._show_response_status

    def record_status(text):
        status_threads.append(QThread.currentThread())
        statuses.append(text)
        original_show_status(text)

    chat._show_response_status = record_status
    try:
        assert chat._start_request("プロセスを確認して", clear_input=True)
        worker_thread = chat._thread
        qtbot.waitUntil(lambda: chat._thread is None, timeout=3000)
        qapp.processEvents()
        assert statuses == ["Windowsの状態を調査しています…", "調査結果を確認しています…"]
        assert status_threads == [qapp.thread(), qapp.thread()]
        assert chat.response_bubble.label.document().toPlainText() == "確認しました"
        assert not worker_thread.isRunning()
        assert not any("Cannot create children for a parent that is in a different thread" in warning
                           for warning in warnings)
        assert not any("QThread: Destroyed while thread is still running" in warning
                           for warning in warnings)
    finally:
        chat.close()
        qapp.processEvents()
        qInstallMessageHandler(previous_handler)


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
    close_chat(chat, qapp)

def test_search_state_only_enables_search_cancel(qapp):
    chat = make_chat(qapp)
    assert not chat.search_in_progress and chat.cancel_search() is False
    chat._on_search_started(); assert chat.search_in_progress
    chat._on_search_completed({}); assert not chat.search_in_progress
    chat._on_finished("done"); assert chat.send_button.isEnabled()
    close_chat(chat, qapp)

def test_search_progress_replaces_status_and_keeps_only_final_reply(qapp):
    chat = make_chat(qapp)
    chat._pending = True
    chat._on_search_started()
    assert chat.search_in_progress
    assert chat.response.text() == "ファイルを検索しています…"
    chat._on_search_completed({"count": 3})
    assert not chat.search_in_progress
    assert chat.response.text() == "検索結果を整理しています…"
    chat._on_delta("3件見つかりました。")
    assert chat.response.text() == "3件見つかりました。"
    assert chat._reply_text == "3件見つかりました。"
    chat._on_finished("3件見つかりました。")
    assert chat.conversation.messages()[-1]["content"] == "3件見つかりました。"
    close_chat(chat, qapp)

def test_normal_chat_does_not_show_search_progress(qapp):
    chat = make_chat(qapp)
    chat._pending = True
    chat._on_delta("通常の回答")
    assert chat.response.text() == "通常の回答"
    close_chat(chat, qapp)

def test_failed_request_resets_search_state_and_input(qapp):
    chat = make_chat(qapp); chat._on_search_started(); assert chat.search_in_progress
    chat._on_failed("network", "接続できませんでした")
    assert not chat.search_in_progress and chat.send_button.isEnabled() and not chat.pending
    close_chat(chat, qapp)


def test_clear_is_blocked_during_request_and_history_reflects_conversation(qapp):
    chat = make_chat(qapp); chat.conversation.add_user("question"); chat.conversation.add_assistant("answer")
    chat._pending = True; assert chat.clear_messages() is False; assert chat.conversation.messages()
    chat._pending = False; assert chat.clear_messages() is True; assert not chat.conversation.messages()
    chat.conversation.add_user("new"); chat.conversation.add_assistant("reply")
    history = HistoryWindow(chat.conversation); history.show(); qapp.processEvents()
    assert "new" in [w.text() for w in history.findChildren(type(chat.response))]
    history.close(); close_chat(chat, qapp)


def test_history_actions_resync_after_completion_and_failure(qapp):
    chat = make_chat(qapp)
    chat.conversation.add_user('old'); chat.conversation.add_assistant('reply')
    chat.show_history(); history = chat.history_window; qapp.processEvents()
    chat.input.setPlainText('new'); assert chat.send_message() is True
    assert not history.clear_button.isEnabled()
    chat._on_finished('answer'); qapp.processEvents(); assert history.clear_button.isEnabled()
    before = chat.conversation.messages(); chat.input.setPlainText('again'); assert chat.send_message() is True
    chat._on_failed('cancelled', 'キャンセル'); assert history.clear_button.isEnabled()
    assert chat.conversation.messages() == before
    assert chat._retry_text == 'again'
    close_chat(chat, qapp)


def test_history_refresh_replaces_rows_without_duplicates(qapp):
    chat = make_chat(qapp); chat.conversation.add_user('質問'); chat.conversation.add_assistant('回答')
    history = HistoryWindow(chat.conversation); history.show()
    for _ in range(3): history.refresh(); qapp.processEvents()
    assert len(history.body.findChildren(QFrame, 'message-card')) == 2
    chat.conversation.add_user('次'); chat.conversation.add_assistant('答'); history.refresh(); qapp.processEvents()
    assert len(history.body.findChildren(QFrame, 'message-card')) == 4
    chat.conversation.clear(); history.refresh(); qapp.processEvents()
    assert not history.copy_button.isEnabled() and not history.clear_button.isEnabled()
    history.close(); close_chat(chat, qapp)


def test_context_menu_and_history_use_japanese_labels(qapp, tmp_path):
    animations = load_animations(Path("assets/animations"))
    pet = PetWindow(animations, tmp_path / "position.json")
    menu = pet._build_context_menu()
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels == [
            "キャラクター管理",
            "キャラクター設定", "OpenAI API 設定", "ファイル検索設定", "自発発話設定", "PC調査情報", "Personal Memory", "最近の検索結果", "処理をキャンセル",
        "チャットを開く", "チャットを閉じる", "会話履歴", "使い方", "位置をリセット", "終了",
    ]
    history = HistoryWindow(pet.input_bubble.conversation)
    assert history.windowTitle() == "会話履歴"
    history.close(); pet.close()


def test_system_tray_uses_character_manager_label(qapp, tmp_path, monkeypatch):
    animations = load_animations(Path("assets/animations"))
    pet = PetWindow(animations, tmp_path / "position.json")
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    assert pet.setup_system_tray()
    assert "キャラクター管理" in [action.text() for action in pet.tray_menu.actions()]
    pet.tray_icon.hide(); pet.close()


def test_response_auto_hide_and_pinned(monkeypatch, qapp):
    timers = []
    monkeypatch.setattr("windows_pet.chat_bubble.QTimer.singleShot", lambda ms, fn: timers.append(fn))
    chat = make_chat(qapp); chat._complete(); assert timers; timers.pop()(); assert not chat.response.isVisible()
    chat.response.show(); chat.set_response_pinned(True); chat._complete(); assert not timers; assert chat.response.isVisible(); close_chat(chat, qapp)

def test_input_height_contract_and_reset(qapp):
    chat = make_chat(qapp)
    assert 48 <= chat.input.height() <= 130
    chat.input.setPlainText("\n".join(["line"] * 8)); qapp.processEvents()
    grown = chat.input.height()
    chat.input.setPlainText("\n".join(["line"] * 40)); qapp.processEvents()
    assert grown > 48 and chat.input.height() <= 130
    chat.input.clear(); qapp.processEvents(); assert 48 <= chat.input.height() <= 50
    close_chat(chat, qapp)

def test_input_event_filter_handles_focus_and_non_focus_events(qapp):
    chat = make_chat(qapp)
    states = []
    chat.focus_state_changed.connect(states.append)
    other = QWidget()
    for event in (QFocusEvent(QEvent.Type.FocusIn), QFocusEvent(QEvent.Type.FocusOut),
                  QPaintEvent(chat.input.rect()), QMouseEvent(QMouseEvent.Type.MouseMove,
                  QPointF(1, 1), Qt.NoButton, Qt.NoButton, Qt.NoModifier),
                  QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)),
                  QEvent(QEvent.Type.Leave)):
        assert chat.eventFilter(chat.input, event) is False
    assert states == [True, False]
    chat.eventFilter(other, QFocusEvent(QEvent.Type.FocusIn))
    assert states == [True, False]
    other.deleteLater(); close_chat(chat, qapp)

def test_response_empty_and_simultaneous_bubbles_are_safe(qapp):
    chat = make_chat(qapp)
    chat.response_bubble.setText("answer"); chat._position_response(); chat.response_bubble.show(); qapp.processEvents()
    assert chat.response_bubble.y() + chat.response_bubble.height() <= qapp.primaryScreen().availableGeometry().bottom() + 1
    chat._on_finished("")
    assert not chat.response_bubble.isVisible()
    close_chat(chat, qapp)


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
    assert response.y() == 300 - 100 - 3
    assert 300 - (response.y() + 100) == 3
    assert response_position(QRect(400, 0, 100, 100), screen, (280, 100)).y() > 100
    assert _error(RuntimeError("401 unauthorized")).kind == "auth"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("windows_pet.ai_client.get_api_key", lambda: None)
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
        assert pet.frameGeometry().top() - (chat.response_bubble.y() + chat.response_bubble.panel_bottom_local_y()) == 3
        assert chat.response_bubble._tail_direction == "bottom"
        assert chat.response_bubble._tail_x == pet.frameGeometry().center().x() - chat.response_bubble.x()
    close_chat(chat, qapp)

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
    close_chat(chat, qapp)


def test_response_bubble_keeps_plain_text_and_scrolls_long_answers(qapp):
    chat = make_chat(qapp)
    text = '<b>タグ</b>\n' + ('長い回答です。' * 300)
    chat.response_bubble.setText(text)
    chat.response_bubble.show(); qapp.processEvents()
    assert chat.response_bubble.toPlainText() == text
    assert chat.response_bubble.label.toPlainText() == text
    assert chat.response_bubble.width() == 420
    assert chat.response_bubble.height() <= 320 + 18
    assert chat.response_bubble.label.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert chat.response_bubble.label.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    close_chat(chat, qapp)


def test_completed_response_renders_safe_markdown_but_preserves_source_and_copy(qapp):
    bubble = ResponseBubble()
    source = "**太字** と *斜体* と `inline`\n- 箇条書き\n- 二つ目\n1. 番号\n2. 二番\n```\nprint('code')\n```"
    bubble.set_markdown_text(source)
    rendered_html = bubble.label.document().toHtml()
    assert bubble.toPlainText() == source
    assert "**" not in bubble.label.toPlainText()
    assert "font-weight:700" in rendered_html and "font-style:italic" in rendered_html
    assert "<ul" in rendered_html and "<ol" in rendered_html and "<pre" in rendered_html
    bubble.set_copy_enabled(True)
    QApplication.clipboard().setText("")
    copy_action = next(action for action in bubble._build_context_menu().actions()
                       if action.text() == "回答をコピー")
    copy_action.trigger()
    assert QApplication.clipboard().text() == source
    bubble.close()


def test_safe_markdown_rejects_html_links_and_all_resources(qapp):
    class TrackingBrowser(SafeMarkdownBrowser):
        def __init__(self):
            super().__init__()
            self.resource_requests = []

        def loadResource(self, resource_type, name):
            self.resource_requests.append(str(name))
            return super().loadResource(resource_type, name)

    browser = TrackingBrowser()
    source = ("<b>危険</b>\n![x](https://example.invalid/a.png)\n"
              "![local](file:///C:/secret.png)\n[リンク](https://example.invalid)")
    browser.set_markdown_text(source)
    rendered_html = browser.document().toHtml()
    assert browser.toPlainText() == "<b>危険</b>\nx\nlocal\nリンク"
    assert "&lt;b&gt;危険&lt;/b&gt;" in rendered_html
    assert "src=" not in rendered_html and "href=" not in rendered_html
    assert not browser.resource_requests
    assert not browser.openLinks() and not browser.openExternalLinks()
    assert not (browser.textInteractionFlags() & Qt.LinksAccessibleByMouse)
    browser.close()


def test_streaming_and_status_stay_plain_then_finished_becomes_markdown(qapp):
    chat = make_chat(qapp)
    chat._on_delta("**未完成")
    assert chat.response_bubble.label.toPlainText() == "**未完成"
    chat._show_response_status("**検索中**")
    assert chat.response_bubble.label.toPlainText() == "**検索中**"
    chat._on_finished("**完成**")
    assert chat.response_bubble.toPlainText() == "**完成**"
    assert chat.response_bubble.label.toPlainText() == "完成"
    close_chat(chat, qapp)


def test_history_renders_only_assistant_markdown_and_copies_raw_source(qapp):
    chat = make_chat(qapp)
    user_source, assistant_source = "**ユーザーはそのまま**", "**assistant**\n- item"
    chat.conversation.add_user(user_source)
    chat.conversation.add_assistant(assistant_source)
    history = HistoryWindow(chat.conversation)
    assistant = history.body.findChild(SafeMarkdownBrowser, "assistant-message")
    user = next(label for label in history.body.findChildren(QLabel) if label.text() == user_source)
    assert assistant is not None and assistant.toPlainText() == "assistant\nitem"
    assert user.textFormat() == Qt.PlainText and user.text() == user_source
    history.copy_conversation()
    assert assistant_source in QApplication.clipboard().text()
    assert user_source in QApplication.clipboard().text()
    history.close(); close_chat(chat, qapp)


def test_response_actions_pin_unpin_and_close_preserve_history(qapp):
    chat = make_chat(qapp)
    chat._pending = False
    chat._on_finished('回答本文')
    chat.response_bubble.show()
    chat.response_bubble._toggle_pin()
    assert chat.response_pinned is True
    chat.response_bubble._toggle_pin()
    assert chat.response_pinned is False
    assert chat.response_bubble.isVisible()
    assert chat.close_response() is True
    assert not chat.response_bubble.isVisible()
    assert chat.conversation.messages()[-1]['content'] == '回答本文'
    close_chat(chat, qapp)


def test_response_actions_are_disabled_while_processing(qapp):
    chat = make_chat(qapp)
    chat._pending = True
    chat._on_search_started()
    assert chat.response_bubble._copy_enabled is False
    assert chat.response_bubble._actions_enabled is False
    assert chat.response_bubble._pending_parent() is True
    assert chat.close_response() is False
    close_chat(chat, qapp)


def test_response_short_text_is_compact_and_menu_order_is_stable(qapp):
    chat = make_chat(qapp)
    chat.response_bubble.setText('考え中…')
    assert 90 <= chat.response_bubble.width() < 420
    assert chat.response_bubble.label.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    chat._on_finished('短い回答')
    chat.response_bubble.show()
    menu = chat.response_bubble._build_context_menu()
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == ['回答をコピー', '回答を固定', '閉じる']
    assert menu.actions()[2].isSeparator()
    close_chat(chat, qapp)


def test_new_send_clears_pin_but_rejected_send_does_not(qapp):
    chat = make_chat(qapp)
    chat.response_pinned = True
    chat.input.clear()
    assert chat.send_message() is False and chat.response_pinned is True
    chat.input.setPlainText('new question')
    assert chat.send_message() is True and chat.response_pinned is False
    assert chat.send_message() is False and chat.response_pinned is False
    close_chat(chat, qapp)

def test_retry_consumes_only_after_success_and_preserves_draft(qapp, monkeypatch):
    chat = make_chat(qapp)
    chat._retry_text = '質問A'; chat.input.setPlainText('次の下書き')
    calls = []
    monkeypatch.setattr(chat, '_start_request', lambda text, clear_input: calls.append((text, clear_input)) or True)
    assert chat.retry_last_request() is True
    assert chat._retry_text is None and chat.input.toPlainText() == '次の下書き'
    assert calls == [('質問A', False)]
    close_chat(chat, qapp)

def test_retry_rejection_preserves_retry_state_and_draft(qapp, monkeypatch):
    chat = make_chat(qapp)
    chat._retry_text = '質問A'; chat.input.setPlainText('次の下書き'); chat._pending = True
    before = chat.conversation.messages()
    assert chat.retry_last_request() is False
    assert chat._retry_text == '質問A' and chat.input.toPlainText() == '次の下書き'
    assert chat.conversation.messages() == before
    close_chat(chat, qapp)

def test_cancel_current_request_is_single_shot_and_preserves_history(qapp):
    chat = make_chat(qapp)
    assert chat.send_button.toolTip() == '送信' and chat.cancel_current_request() is False
    chat._pending = True; chat._worker = FakeWorker([]); chat._active_user_text = '質問A'
    chat.conversation.add_user('質問A'); chat._update_primary_button()
    assert chat.send_button.text() == '■' and chat.cancel_current_request() is True
    assert chat.cancel_requested and not chat.send_button.isEnabled() and chat.pending
    assert chat.cancel_current_request() is False and chat._worker.cancelled
    assert chat.conversation.messages() == [{'role': 'user', 'content': '質問A'}]
    chat._on_failed('cancelled', '処理をキャンセルしました。')
    assert not chat.pending and chat._retry_text == '質問A'
    chat._worker = None
    close_chat(chat, qapp)

def test_missing_key_preserves_input_and_requests_settings(qapp, monkeypatch):
    chat = make_chat(qapp); requested = []; chat.api_settings_requested.connect(lambda: requested.append(True))
    monkeypatch.setattr('windows_pet.chat_bubble.is_api_key_configured', lambda: False)
    chat.input.setPlainText('質問A')
    assert chat.send_message() is False
    assert chat.input.toPlainText() == '質問A' and not chat.conversation.messages()
    assert chat._last_error_kind == 'missing_key' and requested == [True]
    assert chat._thread is None and chat._worker is None
    close_chat(chat, qapp)

def test_configuration_error_requests_settings_but_network_does_not(qapp):
    chat = make_chat(qapp); requested = []; chat.api_settings_requested.connect(lambda: requested.append(True))
    chat._active_user_text = '質問A'; chat.conversation.add_user('質問A'); chat._pending = True
    chat._on_failed('auth', '安全な認証エラー')
    assert chat._last_error_kind == 'auth' and chat._retry_text == '質問A'
    qapp.processEvents(); assert requested == [True]
    requested.clear(); chat._active_user_text = '質問B'; chat.conversation.add_user('質問B'); chat._pending = True
    chat._on_failed('network', '安全なネットワークエラー'); qapp.processEvents(); assert requested == []
    close_chat(chat, qapp)


def test_unpin_schedules_hide_and_repin_invalidates_old_timer(monkeypatch, qapp):
    timers = []
    monkeypatch.setattr('windows_pet.chat_bubble.QTimer.singleShot', lambda ms, fn: timers.append(fn))
    chat = make_chat(qapp)
    chat._on_finished('回答')
    chat.response_bubble.show()
    chat.set_response_pinned(True)
    chat.set_response_pinned(False)
    assert chat.response_bubble.isVisible() and timers
    old_timer = timers.pop()
    chat.set_response_pinned(True)
    old_timer()
    assert chat.response_bubble.isVisible()
    close_chat(chat, qapp)

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
    close_chat(chat, qapp)


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

def test_help_window_is_plain_text_and_reused(qapp, tmp_path):
    animations = load_animations(Path("assets/animations"))
    pet = PetWindow(animations, tmp_path / "position.json")
    before = pet.conversation if hasattr(pet, 'conversation') else pet.input_bubble.conversation.messages()
    pet.show_help(); first = pet.help_window; qapp.processEvents()
    assert first.windowTitle() == "WindowsPet の使い方"
    assert first.browser.isReadOnly() and first.browser.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert first.browser.toPlainText().find("左クリック") >= 0
    assert "検索をキャンセル" not in first.browser.toPlainText()
    first.browser.setPlainText("<b>test</b>"); assert first.browser.toPlainText() == "<b>test</b>"
    first.hide(); pet.show_help(); assert pet.help_window is first and first.isVisible()
    assert pet.input_bubble.conversation.messages() == (before if isinstance(before, list) else before.messages())
    pet.close()

def test_system_tray_unavailable_keeps_pet_usable(qapp, tmp_path, monkeypatch):
    animations = load_animations(Path("assets/animations"))
    pet = PetWindow(animations, tmp_path / "position.json")
    monkeypatch.setattr("windows_pet.main.QSystemTrayIcon.isSystemTrayAvailable", staticmethod(lambda: False))
    assert pet.setup_system_tray() is False and pet.tray_icon is None and pet.tray_menu is None
    pet.show_pet(); assert pet.isVisible(); pet.close()


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
