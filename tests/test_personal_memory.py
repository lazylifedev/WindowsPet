from __future__ import annotations

from datetime import datetime, timedelta, timezone


def make_service(path):
    from windows_pet.memory.service import MemoryService
    from windows_pet.memory.sqlite_repository import SQLiteMemoryRepository
    return MemoryService(SQLiteMemoryRepository(path))


def test_explicit_memory_persists_across_restart_and_reinforces(tmp_path):
    path = tmp_path / "memory.sqlite3"
    first = make_service(path)
    saved = first.request_memory_store(category="preference", key="preferred_editor", value="VS Code")
    assert saved is not None and saved.kind.value == "long_term"
    saved_again = make_service(path).request_memory_store(category="preference", key="preferred_editor", value="VS Code")
    assert saved_again is not None and saved_again.memory_id == saved.memory_id and saved_again.reinforcement_count == 2
    assert make_service(path).lookup("preferred_editor", limit=5)[0].value == "VS Code"


def test_protected_memory_survives_cleanup_but_explicit_forget_deletes(tmp_path):
    service = make_service(tmp_path / "memory.sqlite3")
    record = service.remember(category="fact", key="important_fact", value="keep this", protected=True)
    assert record is not None and service.cleanup_candidates(now=datetime.now(timezone.utc) + timedelta(days=365), stale_days=0) == []
    assert service.forget(record.memory_id)
    assert service.lookup("important_fact") == []


def test_short_term_expiry_is_not_returned_and_cleanup_does_not_remove_protected(tmp_path):
    service = make_service(tmp_path / "memory.sqlite3")
    record = service.remember(category="fact", key="temporary", value="soon gone", short_term_ttl=-1)
    assert record is not None and service.lookup("temporary") == []
    assert service.repository.delete_expired(datetime.now(timezone.utc)) == 1


def test_privacy_filter_rejects_secrets_and_oversize_without_writing(tmp_path):
    service = make_service(tmp_path / "memory.sqlite3")
    assert service.request_memory_store(category="fact", key="api_key", value="sk-abcdefghijklmnop") is None
    assert service.request_memory_store(category="fact", key="notes", value="x" * 2001) is None
    assert service.list() == []


def test_corrupt_database_falls_back_without_crashing(tmp_path):
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not sqlite")
    service = make_service(path)
    assert service.lookup() == []
    assert service.request_memory_store(category="fact", key="x", value="y") is None


def test_memory_command_parser_supports_explicit_store_and_forget():
    from windows_pet.memory.commands import parse_memory_command

    command = parse_memory_command("覚えておいて preference: preferred_editor = VS Code")
    assert command is not None and command.action == "remember" and command.protected
    forget = parse_memory_command("忘れて preferred_editor")
    assert forget is not None and forget.action == "forget"


def test_memory_window_lists_and_deletes_selected_record(qapp, tmp_path):
    from windows_pet.memory_window import MemoryWindow

    service = make_service(tmp_path / "memory.sqlite3")
    record = service.remember(category="preference", key="preferred_editor", value="VS Code")
    window = MemoryWindow(service)
    assert record is not None and window.list_widget.count() == 1
    window.list_widget.setCurrentRow(0)
    assert window.delete_selected()
    assert window.list_widget.count() == 0
    window.close()
