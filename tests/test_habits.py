from datetime import datetime, timezone

import pytest

from windows_pet.habits import HabitService, SQLiteHabitRepository


def service(tmp_path):
    return HabitService(SQLiteHabitRepository(tmp_path / "habits.sqlite3"))


def test_repeated_abstract_action_across_days_consolidates_and_bounds_raw_observations(tmp_path):
    habits = service(tmp_path)
    for day in range(3, 6):
        habits.observe(event_type="open_app", target="outlook", observed_at=datetime(2026, 8, day, 8, 32, tzinfo=timezone.utc))

    candidates = habits.consolidate(now=datetime(2026, 8, 6, tzinfo=timezone.utc))

    assert len(candidates) == 1
    assert candidates[0].target == "outlook"
    assert candidates[0].confidence >= 0.65
    assert len(habits.repository.list_observations()) <= 2


def test_one_or_two_events_do_not_become_a_habit(tmp_path):
    habits = service(tmp_path)
    habits.observe(event_type="open_app", target="outlook", observed_at=datetime(2026, 8, 3, 8, 32, tzinfo=timezone.utc))
    habits.observe(event_type="open_app", target="outlook", observed_at=datetime(2026, 8, 4, 8, 32, tzinfo=timezone.utc))
    assert habits.consolidate(now=datetime(2026, 8, 5, tzinfo=timezone.utc)) == []


def test_different_time_bucket_is_not_merged(tmp_path):
    habits = service(tmp_path)
    for day in range(3, 6):
        habits.observe(event_type="open_app", target="outlook", observed_at=datetime(2026, 8, day, 8, 32, tzinfo=timezone.utc))
        habits.observe(event_type="open_app", target="outlook", observed_at=datetime(2026, 8, day, 14, 0, tzinfo=timezone.utc))
    assert len(habits.consolidate(now=datetime(2026, 8, 6, tzinfo=timezone.utc))) == 2


def test_explicit_negative_feedback_reduces_strength_and_stale_decay(tmp_path):
    habits = service(tmp_path)
    for day in range(3, 6):
        habits.observe(event_type="open_app", target="outlook", observed_at=datetime(2026, 8, day, 8, 32, tzinfo=timezone.utc))
    habit = habits.consolidate(now=datetime(2026, 8, 6, tzinfo=timezone.utc))[0]
    reduced = habits.record_feedback(habit.habit_id, positive=False)
    assert reduced is not None and reduced.strength < habit.strength and reduced.confidence < habit.confidence
    decayed = habits.decay_stale(now=datetime(2026, 10, 1, tzinfo=timezone.utc), stale_days=30)
    assert decayed and decayed[0].strength <= reduced.strength


def test_machine_specific_or_raw_habit_data_is_rejected(tmp_path):
    habits = service(tmp_path)
    with pytest.raises(ValueError):
        habits.observe(event_type="open_app", target=r"C:\Users\private\secret.exe")
    with pytest.raises(ValueError):
        habits.observe(event_type="conversation", target="outlook", source="raw stdout")
