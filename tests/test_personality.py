from datetime import datetime, timedelta, timezone

from windows_pet.personality import (
    CasualPermission,
    ContextCompressor,
    RelationshipService,
    RelationshipStage,
    SQLitePersonalityRepository,
)


def service(tmp_path):
    return RelationshipService(SQLitePersonalityRepository(tmp_path / "personality.sqlite3"))


def test_new_install_is_first_meeting_polite_and_bounded(tmp_path):
    pet = service(tmp_path)
    assert pet.state().stage is RelationshipStage.FIRST_MEETING
    assert pet.preferences().formality == "polite"
    context = pet.bounded_context()
    assert context["relationship"] == "first_meeting"
    assert context["speech_preferences"]["formality"] == "polite"


def test_relationship_progression_is_slow_and_negative_feedback_does_not_raise_it(tmp_path):
    pet = service(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for day in range(4):
        state = pet.record_interaction(at=start + timedelta(days=day), meaningful=True, verified_assistance_success=True, positive=True)
    assert state.stage in {RelationshipStage.ACQUAINTED, RelationshipStage.COMFORTABLE}
    assert state.stage is not RelationshipStage.PARTNER
    reduced = pet.record_interaction(at=start + timedelta(days=5), negative=True)
    assert reduced.familiarity < state.familiarity
    assert reduced.stage is not RelationshipStage.PARTNER


def test_casual_permission_is_asked_once_and_respects_keep_polite(tmp_path):
    pet = service(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for day in range(24):
        pet.record_interaction(at=start + timedelta(days=day), meaningful=True)
    at = start + timedelta(days=24)
    assert pet.state().stage is RelationshipStage.COMFORTABLE
    assert pet.casual_speech_candidate(at=at)
    assert not pet.casual_speech_candidate(at=at + timedelta(days=1))
    state = pet.respond_to_casual_permission(CasualPermission.KEEP_POLITE, at=at)
    assert state.casual_speech_permission is CasualPermission.KEEP_POLITE
    assert not pet.casual_speech_candidate(at=at + timedelta(days=365))


def test_ask_later_can_be_reoffered_only_after_cooldown(tmp_path):
    pet = service(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for day in range(24):
        pet.record_interaction(at=start + timedelta(days=day), meaningful=True)
    at = start + timedelta(days=24)
    assert pet.casual_speech_candidate(at=at)
    pet.respond_to_casual_permission(CasualPermission.ASK_LATER, at=at)
    assert not pet.casual_speech_candidate(at=at + timedelta(days=1))
    assert pet.casual_speech_candidate(at=at + timedelta(days=31))
    pet.respond_to_casual_permission(CasualPermission.ALLOW, at=at + timedelta(days=31))
    assert not pet.casual_speech_candidate(at=at + timedelta(days=100))


def test_context_compressor_bounds_recent_memory_and_evidence(tmp_path):
    compressed = ContextCompressor(max_recent_items=2, max_memory_items=1, max_evidence_items=1, max_item_chars=10).compress(
        goal="g" * 500, recent_task_context=["a" * 50, "b", "c"], relevant_memories=[{"key": "value"}, {"other": "ignored"}],
        relationship_style={"relationship": "comfortable"}, safe_evidence=["e" * 50, "ignored"],
    )
    result = compressed.as_dict()
    assert len(result["goal"]) == 240
    assert len(result["recent_task_context"]) == 2 and len(result["recent_task_context"][0]) == 10
    assert len(result["relevant_memories"]) == 1 and len(result["safe_evidence"]) == 1
