from __future__ import annotations


def experience(**overrides):
    from windows_pet.reflection import Experience

    values = dict(task_id="task-1", intent="launch_app", operation="launch", abstract_target="Notepad", started_at="2026-08-08T00:00:00+00:00", finished_at="2026-08-08T00:00:01+00:00", outcome="succeeded", verification_result="verified", attempt_count=1)
    values.update(overrides)
    return Experience(**values)


def test_verified_success_promotes_only_abstract_local_skill(tmp_path):
    from windows_pet.local_skill_store import LocalSkillStore
    from windows_pet.reflection import ReflectionPipeline

    pipeline = ReflectionPipeline(LocalSkillStore(tmp_path / "skills.sqlite3"))
    item = experience()
    assert pipeline.record_experience(item)
    candidate = pipeline.candidate(item, alias="メモ帳を開いて")
    assert candidate is not None and candidate.verified and pipeline.promote(candidate)
    assert pipeline.skill_store.find_alias("メモ帳を開いて").target == "Notepad"


def test_execution_success_without_verification_is_not_promoted():
    from windows_pet.reflection import ReflectionPipeline

    item = experience(verification_result="unknown")
    pipeline = ReflectionPipeline()
    assert pipeline.reflect(item).reusable is False
    assert pipeline.candidate(item, alias="open it") is None


def test_failed_then_verified_retry_keeps_failure_evidence_but_promotes_final_result(tmp_path):
    from windows_pet.local_skill_store import LocalSkillStore
    from windows_pet.reflection import ReflectionPipeline

    pipeline = ReflectionPipeline(LocalSkillStore(tmp_path / "skills.sqlite3"))
    failed = experience(task_id="failed-1", outcome="failed", verification_result="failed", failure_reason="not found", attempt_count=1)
    final = experience(task_id="success-2", attempt_count=2)
    assert pipeline.record_experience(failed) and pipeline.record_experience(final)
    candidate = pipeline.candidate(final, alias="open notepad")
    assert candidate is not None and pipeline.promote(candidate)
    assert pipeline.experiences[0].failure_reason == "not found"


def test_secret_or_machine_path_never_enters_reflection():
    from windows_pet.reflection import Experience, ReflectionPipeline

    pipeline = ReflectionPipeline()
    unsafe = Experience("task", "launch_app", "launch", r"C:\secret\app.exe", "a", "b", "succeeded", "verified")
    assert not pipeline.record_experience(unsafe)
    assert pipeline.candidate(unsafe, alias="open") is None


def test_secret_provenance_is_rejected():
    from windows_pet.reflection import ReflectionPipeline

    item = experience(provenance_ids=("api_key=secret",))
    assert not ReflectionPipeline().record_experience(item)


def test_revalidation_resolves_current_identity_instead_of_using_saved_path():
    from windows_pet.reflection import revalidate_abstract_target

    calls = []
    result = revalidate_abstract_target("application", "Notepad", lambda kind, target: calls.append((kind, target)) or "C:/Windows/notepad.exe")
    assert result.valid and result.current_identity == "C:/Windows/notepad.exe" and calls == [("application", "Notepad")]
