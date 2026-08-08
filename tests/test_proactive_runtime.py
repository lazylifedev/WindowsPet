from datetime import datetime, timedelta, timezone

from windows_pet.proactive import ProactiveEngine, ProactiveRuntime, ProactiveSettings, SQLiteProactiveRepository


def test_runtime_connects_startup_and_idle_to_speech_callback(tmp_path):
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    spoken = []
    runtime = ProactiveRuntime(ProactiveEngine(SQLiteProactiveRepository(tmp_path / "state.sqlite3")), spoken.append, now=lambda: now, last_activity=lambda: now - timedelta(hours=2))
    assert runtime.startup() and len(spoken) == 1
    assert not runtime.tick()  # cooldown prevents a second bubble


def test_runtime_respects_off_and_critical_operation(tmp_path):
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    spoken = []
    engine = ProactiveEngine(SQLiteProactiveRepository(tmp_path / "state.sqlite3"), ProactiveSettings(enabled=False))
    runtime = ProactiveRuntime(engine, spoken.append, now=lambda: now, critical_operation=lambda: True)
    assert not runtime.startup() and spoken == []
