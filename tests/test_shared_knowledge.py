from datetime import datetime, timedelta, timezone

from windows_pet.shared_knowledge import (
    InMemorySharedKnowledgeRepository,
    LocalOnlyGlobalBrainClient,
    SharedKnowledgeCache,
    SharedKnowledgeSanitizer,
    SQLiteSharedKnowledgeRepository,
    SharedKnowledgeUploadQueue,
    new_installation_evidence_id,
)


def generic():
    return {
        "intent": "launch_app", "target_type": "windows_builtin", "target": "notepad",
        "aliases": ["メモ帳を開いて", "notepadを起動"], "success_count": 10, "failure_count": 1, "confidence": 0.9,
    }


def test_generic_skill_is_eligible_but_personal_data_is_rejected():
    sanitizer = SharedKnowledgeSanitizer()
    assert sanitizer.sanitize(generic()).eligible
    assert not sanitizer.is_eligible({**generic(), "target": r"C:\Users\Yuki\private-tool.exe"})
    assert not sanitizer.is_eligible({**generic(), "aliases": ["user@example.com"]})
    assert not sanitizer.is_eligible({**generic(), "target": "192.168.1.20"})
    assert not sanitizer.is_eligible({**generic(), "habit": "12時に昼休み"})
    assert not sanitizer.is_eligible({**generic(), "preference": "polite"})


def test_cache_requires_local_revalidation_and_never_executes_directly():
    cache = SharedKnowledgeCache(InMemorySharedKnowledgeRepository())
    record = cache.store_candidate(generic())
    assert record is not None
    match = cache.resolve("launch_app", "notepad")
    assert match is not None and match.requires_local_revalidation and not match.stale
    called = []
    assert cache.revalidate(match, lambda item: called.append(item.target) or True)
    assert called == ["notepad"]


def test_stale_shared_skill_cannot_be_revalidated():
    cache = SharedKnowledgeCache(InMemorySharedKnowledgeRepository())
    record = cache.store_candidate({**generic(), "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    assert record is not None
    match = cache.resolve("launch_app", "notepad")
    assert match is not None and match.stale
    assert not cache.revalidate(match, lambda _: True)


def test_sqlite_cache_round_trips_without_network(tmp_path):
    cache = SharedKnowledgeCache(SQLiteSharedKnowledgeRepository(tmp_path / "shared.sqlite3"))
    assert cache.store_candidate(generic()) is not None
    assert cache.resolve("launch_app", "notepad").record.target == "notepad"
    client = LocalOnlyGlobalBrainClient()
    assert client.fetch("launch_app", "notepad") is None
    assert client.publish(cache.resolve("launch_app", "notepad").record) is False


def test_upload_queue_persists_only_sanitized_verified_candidates(tmp_path):
    queue = SharedKnowledgeUploadQueue(tmp_path / "queue.sqlite3", max_items=2)
    assert queue.enqueue_candidate("event-1", generic(), verified_success=True, global_eligible=True)
    assert not queue.enqueue_candidate("event-2", {**generic(), "personal_path": r"C:\Users\Alice\x"}, verified_success=True, global_eligible=True)
    assert not queue.enqueue_candidate("event-3", generic(), verified_success=False, global_eligible=True)
    assert not queue.enqueue_candidate("event-4", generic(), verified_success=True, global_eligible=False)
    item = queue.list_ready()[0]
    assert item.payload["target"] == "notepad"
    assert "personal_path" not in item.payload
    assert queue.flush(lambda upload: upload.event_id == "event-1") == (1, 0)
    assert queue.list_ready() == []


def test_upload_queue_deduplicates_and_keeps_failed_upload_local(tmp_path):
    queue = SharedKnowledgeUploadQueue(tmp_path / "queue.sqlite3")
    assert queue.enqueue_candidate("event-1", generic(), verified_success=True, global_eligible=True)
    assert not queue.enqueue_candidate("event-1", generic(), verified_success=True, global_eligible=True)
    assert queue.flush(lambda _: False) == (0, 1)
    assert queue.list_ready() == []


def test_installation_evidence_id_is_opaque_and_resettable():
    first = new_installation_evidence_id()
    second = new_installation_evidence_id()
    assert first.startswith("install-") and len(first) == 40 and first != second
    assert "Users" not in first and "hostname" not in first
