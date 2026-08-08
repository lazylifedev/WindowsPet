from datetime import datetime, time, timedelta, timezone

from windows_pet.proactive import (
    ProactiveEngine,
    ProactiveSettings,
    ReactionKind,
    SQLiteProactiveRepository,
    TriggerKind,
)


def engine(tmp_path, **kwargs):
    return ProactiveEngine(SQLiteProactiveRepository(tmp_path / "proactive.sqlite3"), ProactiveSettings(**kwargs))


def test_startup_candidate_is_time_aware_and_phrase_family_is_bounded(tmp_path):
    pet = engine(tmp_path)
    now = datetime(2026, 8, 8, 8, 30, tzinfo=timezone.utc)
    candidate = pet.startup_candidate(now)
    assert candidate.trigger is TriggerKind.STARTUP
    assert candidate.phrase_family == "morning"
    assert pet.phrase(candidate) in {"おはようございます。今日もよろしくお願いします。", "おはようございます。無理のないペースでいきましょう。"}


def test_decision_suppresses_quiet_hours_cooldown_daily_cap_and_focus(tmp_path):
    pet = engine(tmp_path, quiet_start=time(22), quiet_end=time(7), daily_cap=1, minimum_cooldown_minutes=30)
    quiet = datetime(2026, 8, 8, 23, tzinfo=timezone.utc)
    candidate = pet.startup_candidate(quiet)
    assert pet.decide(candidate, now=quiet).reason == "quiet_hours"

    active = datetime(2026, 8, 9, 9, tzinfo=timezone.utc)
    candidate = pet.startup_candidate(active)
    assert pet.decide(candidate, now=active, focus_mode=True).reason == "focus_suppression"
    assert pet.decide(candidate, now=active).should_speak
    pet.record_spoken(candidate, now=active)
    assert pet.decide(candidate, now=active + timedelta(minutes=1)).reason == "cooldown"
    assert pet.decide(candidate, now=active + timedelta(hours=1)).reason == "daily_cap"


def test_known_lunch_only_and_idle_return_use_fake_clock(tmp_path):
    pet = engine(tmp_path)
    now = datetime(2026, 8, 8, 11, 52, tzinfo=timezone.utc)
    assert pet.lunch_candidate(now, lunch_start=None) is None
    lunch = pet.lunch_candidate(now, lunch_start=datetime(2026, 8, 8, 12, tzinfo=timezone.utc))
    assert lunch is not None and lunch.trigger is TriggerKind.LUNCH_SOON
    idle = pet.idle_return_candidate(now, last_activity=now - timedelta(minutes=61))
    assert idle is not None and idle.trigger is TriggerKind.IDLE_RETURN


def test_ignored_history_reduces_score_and_explicit_negative_disables_category(tmp_path):
    pet = engine(tmp_path)
    now = datetime(2026, 8, 8, 9, tzinfo=timezone.utc)
    candidate = pet.startup_candidate(now)
    pet.record_reaction(candidate, ReactionKind.IGNORE)
    decision = pet.decide(candidate, now=now)
    assert decision.should_speak
    pet.record_reaction(candidate, ReactionKind.EXPLICIT_NEGATIVE)
    assert pet.decide(candidate, now=now).reason == "category_disabled"


def test_unknown_trigger_category_is_not_spoken_after_disabled_setting(tmp_path):
    pet = engine(tmp_path, enabled=False)
    now = datetime(2026, 8, 8, 9, tzinfo=timezone.utc)
    assert pet.decide(pet.startup_candidate(now), now=now).reason == "disabled_by_user"
