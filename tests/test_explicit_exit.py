from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from windows_pet.animation import load_animations
from windows_pet.file_search_settings_window import FileSearchSettingsWindow
from windows_pet.local_inspection_window import LocalInspectionWindow
from windows_pet.main import PetWindow, configure_application
from windows_pet.openai_settings_window import OpenAISettingsWindow
from windows_pet.search_results_window import SearchResultsWindow


def _pet(tmp_path, quit_callback=lambda: None):
    return PetWindow(
        load_animations(Path("assets/animations")),
        tmp_path / "position.json",
        quit_callback=quit_callback,
    )


def test_auxiliary_windows_close_without_quitting_and_history_is_reusable(qapp, tmp_path):
    """Closing any auxiliary UI only closes that UI, never the QApplication."""
    configure_application(qapp)
    assert not qapp.quitOnLastWindowClosed()
    about_to_quit = []
    qapp.aboutToQuit.connect(lambda: about_to_quit.append(True))
    pet = _pet(tmp_path)
    pet.show_pet()
    conversation = pet.input_bubble.conversation
    conversation.add_user("remember this")
    conversation.add_assistant("still here")

    pet.input_bubble.show_history()
    history = pet.input_bubble.history_window
    history.close()
    qapp.processEvents()
    assert not history.isVisible() and pet.isVisible() and not about_to_quit
    pet.show_pet()
    pet.input_bubble.show_history()
    assert pet.input_bubble.history_window is history
    assert conversation.messages()[0]["content"] == "remember this"
    history.findChildren(type(history.copy_button))[-1].click()
    qapp.processEvents()
    assert not history.isVisible() and not about_to_quit

    for dialog in (
        OpenAISettingsWindow(pet),
        FileSearchSettingsWindow(parent=pet),
        SearchResultsWindow(SimpleNamespace(results=[]), pet),
    ):
        dialog.show()
        dialog.close()
        qapp.processEvents()
        assert not dialog.isVisible() and pet.isVisible() and not about_to_quit

    # Showing this dialog starts a real local inspection.  Its close path is
    # synchronous, so exercise it without showing the window in this fake-only test.
    inspection = LocalInspectionWindow(parent=pet)
    inspection.close()
    qapp.processEvents()
    assert not inspection.isVisible() and pet.isVisible() and not about_to_quit

    pet.show_help()
    pet.help_window.close()
    pet.input_bubble.response_bubble.show()
    pet.input_bubble.response_bubble.close()
    pet.input_bubble.show()
    pet.input_bubble.close()
    qapp.processEvents()
    assert pet.isVisible() and not about_to_quit
    pet.close()


def test_explicit_exit_actions_share_idempotent_quit_path(qapp, tmp_path, monkeypatch):
    configure_application(qapp)
    quit_calls = []
    pet = _pet(tmp_path, quit_callback=lambda: quit_calls.append("quit"))
    pet.show()

    menu = pet._build_context_menu()
    menu.actions()[-1].trigger()
    pet.quit_application()
    assert quit_calls == ["quit"]
    assert pet._exit_requested and pet._shutdown_complete

    pet = _pet(tmp_path, quit_callback=lambda: quit_calls.append("tray"))
    monkeypatch.setattr("windows_pet.main.QSystemTrayIcon.isSystemTrayAvailable", staticmethod(lambda: True))
    assert pet.setup_system_tray()
    pet.tray_menu.actions()[-1].trigger()
    assert quit_calls == ["quit", "tray"]
