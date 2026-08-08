from datetime import datetime, timedelta, timezone

from windows_pet.shared_knowledge import (
    InMemorySharedKnowledgeRepository,
    LocalOnlyGlobalBrainClient,
    SharedKnowledgeCache,
    SharedKnowledgeSanitizer,
    SQLiteSharedKnowledgeRepository,
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
