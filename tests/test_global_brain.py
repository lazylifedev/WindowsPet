from fastapi.testclient import TestClient

from windows_pet_global_brain import GlobalBrainService, InMemoryGlobalBrainRepository, create_app


COMPATIBILITY = {
    "windows_version": "11",
    "architecture": "x64",
    "application_version": "1.0",
    "capability_version": "1",
}


def candidate_payload(candidate_id="candidate-0001", installation="install-0001", **overrides):
    skill = {
        "intent": "launch_app",
        "target_type": "windows_builtin",
        "target": "notepad",
        "aliases": ["open notepad"],
        "compatibility": COMPATIBILITY,
    }
    skill.update(overrides.pop("skill", {}))
    return {
        "candidate_id": candidate_id,
        "installation_evidence_id": installation,
        "verified_success": True,
        "skill": skill,
        **overrides,
    }


def test_local_global_brain_api_accepts_candidate_and_promotes_after_distinct_verified_evidence():
    service = GlobalBrainService(InMemoryGlobalBrainRepository())
    client = TestClient(create_app(service))

    accepted = client.post("/v1/candidates", json=candidate_payload()).json()
    assert accepted["accepted"] and accepted["trust_state"] in {"candidate", "observed"}
    knowledge_id = accepted["knowledge_id"]
    skill = service.repository.get_skill(knowledge_id)

    assert client.post(
        "/v1/results",
        json={
            "event_id": "event-0002",
            "knowledge_id": knowledge_id,
            "knowledge_version": skill.knowledge_version,
            "installation_evidence_id": "install-0002",
            "compatibility": COMPATIBILITY,
            "verified_success": True,
        },
    ).json()["accepted"]
    skill = service.repository.get_skill(knowledge_id)
    result = client.post(
        "/v1/results",
        json={
            "event_id": "event-0003",
            "knowledge_id": knowledge_id,
            "knowledge_version": skill.knowledge_version,
            "installation_evidence_id": "install-0002",
            "compatibility": COMPATIBILITY,
            "verified_success": True,
        },
    ).json()
    assert result["promoted"] is True

    lookup = client.post(
        "/v1/knowledge/lookup",
        json={"intent": "launch_app", "target": "notepad", "compatibility": COMPATIBILITY},
    ).json()
    assert len(lookup["matches"]) == 1
    assert lookup["matches"][0]["trust_state"] == "trusted"


def test_single_installation_spam_never_becomes_trusted_and_duplicate_event_is_deduplicated():
    service = GlobalBrainService(InMemoryGlobalBrainRepository())
    client = TestClient(create_app(service))
    accepted = client.post("/v1/candidates", json=candidate_payload()).json()
    knowledge_id = accepted["knowledge_id"]

    for index in range(4):
        skill = service.repository.get_skill(knowledge_id)
        response = client.post(
            "/v1/results",
            json={
                "event_id": f"event-spam-{index:04d}",
                "knowledge_id": knowledge_id,
                "knowledge_version": skill.knowledge_version,
                "installation_evidence_id": "install-0001",
                "compatibility": COMPATIBILITY,
                "verified_success": True,
            },
        )
        assert response.status_code == 200
    skill = service.repository.get_skill(knowledge_id)
    assert skill.trust_state.value != "trusted"
    duplicate = client.post(
        "/v1/results",
        json={
            "event_id": "event-spam-0000",
            "knowledge_id": knowledge_id,
            "knowledge_version": skill.knowledge_version,
            "installation_evidence_id": "install-0001",
            "compatibility": COMPATIBILITY,
            "verified_success": True,
        },
    ).json()
    assert duplicate["accepted"] and duplicate["duplicate"]


def test_server_privacy_boundary_and_verified_success_gate():
    client = TestClient(create_app())
    assert client.post("/v1/candidates", json=candidate_payload(verified_success=False)).json()["reason"] == "verified_success_required"
    private = candidate_payload(skill={"target": r"C:\Users\Alice\private-tool.exe"})
    assert client.post("/v1/candidates", json=private).json()["accepted"] is False
    secret = candidate_payload(skill={"aliases": ["use api_key=secret"]})
    assert client.post("/v1/candidates", json=secret).json()["accepted"] is False
    shell = candidate_payload(skill={"target_type": "powershell_script"})
    assert client.post("/v1/candidates", json=shell).json()["accepted"] is False
    habit = candidate_payload(habit="morning")
    assert client.post("/v1/candidates", json=habit).status_code == 422


def test_lookup_rejects_stale_client_version_and_unknown_fields():
    client = TestClient(create_app())
    stale = client.post(
        "/v1/knowledge/lookup",
        json={"intent": "launch_app", "target": "notepad", "compatibility": COMPATIBILITY, "client_knowledge_version": "v0"},
    )
    assert stale.status_code == 200 and stale.json()["stale_client"] is True and stale.json()["matches"] == []
    unknown = client.post(
        "/v1/knowledge/lookup",
        json={"intent": "launch_app", "target": "notepad", "compatibility": COMPATIBILITY, "unexpected": "reject"},
    )
    assert unknown.status_code == 422


def test_high_failure_ratio_does_not_promote():
    service = GlobalBrainService(InMemoryGlobalBrainRepository())
    client = TestClient(create_app(service))
    accepted = client.post("/v1/candidates", json=candidate_payload()).json()
    knowledge_id = accepted["knowledge_id"]
    outcomes = [
        ("event-fail-1", "install-0002", False, "timeout"),
        ("event-success-2", "install-0003", True, None),
        ("event-success-3", "install-0004", True, None),
        ("event-fail-2", "install-0005", False, "incompatible"),
    ]
    last = None
    for event_id, installation, success, failure in outcomes:
        skill = service.repository.get_skill(knowledge_id)
        last = client.post(
            "/v1/results",
            json={
                "event_id": event_id,
                "knowledge_id": knowledge_id,
                "knowledge_version": skill.knowledge_version,
                "installation_evidence_id": installation,
                "compatibility": COMPATIBILITY,
                "verified_success": success,
                "failure_category": failure,
            },
        ).json()
    assert last["accepted"] and last["promoted"] is False and last["trust_state"] == "rejected"
    assert service.repository.get_skill(knowledge_id).failure_count == 2


def test_unknown_knowledge_result_is_rejected_without_creating_state():
    client = TestClient(create_app())
    response = client.post(
        "/v1/results",
        json={
            "event_id": "event-unknown",
            "knowledge_id": "knowledge-missing",
            "knowledge_version": "v1",
            "installation_evidence_id": "install-0001",
            "compatibility": COMPATIBILITY,
            "verified_success": True,
        },
    )
    assert response.status_code == 200 and response.json()["reason"] == "unknown_knowledge"


def test_firestore_adapter_uses_injected_collections_without_google_credentials():
    from windows_pet_global_brain import FirestoreGlobalBrainRepository

    class Document:
        def __init__(self, store):
            self.store = store

        def set(self, value):
            self.store.append(value)

    class Collection:
        def __init__(self, store):
            self.store = store

        def document(self, _document_id):
            return Document(self.store)

    class FakeClient:
        def __init__(self):
            self.collections = {}

        def collection(self, name):
            self.collections.setdefault(name, [])
            return Collection(self.collections[name])

    fake = FakeClient()
    service = GlobalBrainService(FirestoreGlobalBrainRepository(fake))
    result = service.submit_candidate(
        candidate_id="candidate-0001",
        installation_evidence_id="install-0001",
        skill_data={
            "intent": "launch_app",
            "target_type": "windows_builtin",
            "target": "notepad",
            "aliases": ["open notepad"],
            "compatibility": ["windows_version=11", "architecture=x64"],
        },
        verified_success=True,
    )
    assert result.accepted
    assert set(fake.collections) >= {"global_skills", "knowledge_candidates", "evidence_aggregates"}
