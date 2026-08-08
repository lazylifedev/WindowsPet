from __future__ import annotations


def test_local_skill_store_persists_abstract_verified_skill(tmp_path):
    from windows_pet.local_skill_store import LocalSkillStore

    store = LocalSkillStore(tmp_path / "skills.sqlite3")
    assert store.record_success(intent="launch_app", target_type="application", target="メモ帳", alias="メモ帳を開いて")
    skill = store.find_alias(" メモ帳を開いて ")
    assert skill is not None
    assert skill.target == "メモ帳"
    assert skill.success_count == 1
    assert skill.failure_count == 0
    assert skill.memory_strength > 0.5


def test_local_skill_store_tracks_failure_without_treating_cancel_as_success(tmp_path):
    from windows_pet.local_skill_store import LocalSkillStore

    store = LocalSkillStore(tmp_path / "skills.sqlite3")
    assert store.record_failure(intent="launch_app", target_type="application", target="メモ帳", alias="メモ帳を開いて")
    skill = store.find_alias("メモ帳を開いて")
    assert skill is not None
    assert skill.success_count == 0 and skill.failure_count == 1
    assert skill.memory_strength < 0.5


def test_local_skill_store_does_not_persist_secret_looking_alias(tmp_path):
    from windows_pet.local_skill_store import LocalSkillStore

    store = LocalSkillStore(tmp_path / "skills.sqlite3")
    assert not store.record_success(intent="launch_app", target_type="application", target="Editor", alias="token=secret-value")
    assert store.find_alias("token=secret-value") is None


def test_local_router_uses_builtins_and_learned_aliases_without_paths(tmp_path):
    from windows_pet.local_skill_router import LocalSkillRouter
    from windows_pet.local_skill_store import LocalSkillStore

    store = LocalSkillStore(tmp_path / "skills.sqlite3")
    router = LocalSkillRouter(store)
    built_in = router.route("メモ帳を起動して")
    assert built_in is not None and built_in.source == "built_in"
    assert built_in.request.application_name == "メモ帳"
    assert built_in.request.exact_path is None

    assert store.record_success(intent="launch_app", target_type="application", target="電卓", alias="計算機を開いて")
    learned = router.route("計算機を開いて")
    assert learned is not None and learned.source == "learned"
    assert learned.request.application_name == "電卓"
    assert learned.request.exact_path is None
    assert router.route("任意のコマンドを実行して") is None


def test_local_router_does_not_start_worker_or_require_api_key(qapp, tmp_path):
    from windows_pet.chat_bubble import InputBubble
    from windows_pet.local_skill_router import LocalSkillRouter
    from windows_pet.local_skill_store import LocalSkillStore

    class Pet:
        def play(self, _): pass

    def forbidden_worker(_):
        raise AssertionError("cloud worker must not be created")

    chat = InputBubble(Pet(), worker_factory=forbidden_worker, local_skill_router=LocalSkillRouter(LocalSkillStore(tmp_path / "skills.sqlite3")))
    received = []
    chat.application_launch_ready.connect(received.append)
    chat.input.setPlainText("メモ帳を開いて")
    assert chat.send_message()
    qapp.processEvents()
    assert received and received[0].application_name == "メモ帳"
    assert received[0].source == "local_skill"
    chat.close()
