from __future__ import annotations

from windows_pet.action_models import SideEffect
from windows_pet.audit_log import InMemoryAuditSink
from windows_pet.reflection import FakeLLMReflectionProvider, OptionalLLMReflection, ReflectionEnrichment, ReflectionPipeline
from windows_pet.research import *


def action_plan(goal_id: str, *, plan_id: str = "plan-1") -> CandidatePlan:
    action = PlanStep("action", PlanStepKind.ACTION_PROPOSAL, "service_control", "restart_service", "restart the selected service", SideEffect.SYSTEM_CHANGE.value, True, True, (), "service state")
    verify = PlanStep("verify", PlanStepKind.VERIFY, "service_inspection", "inspect_services", "read the service state", SideEffect.READ_ONLY.value, False, False, (), "service state is running")
    return CandidatePlan(plan_id, goal_id, (action, verify), "service is running", True, True, RiskClass.ADMIN, "read service state independently")


def test_registry_is_code_owned_and_effect_cannot_be_overridden():
    registry = default_capability_registry()
    assert registry.get("service_control").requires_confirmation
    assert registry.get("service_control").requires_admin
    try:
        registry.register(Capability("bad", ("x",), SideEffect.SYSTEM_CHANGE, False, False, True))
    except ValueError as exc:
        assert str(exc) == "read_only_capability_has_side_effect"
    else:
        raise AssertionError("unsafe capability accepted")


def test_evidence_trust_and_bounded_ledger():
    from windows_pet.research.evidence import EvidenceLedger, evidence_trust
    assert evidence_trust(EvidenceSource.LOCAL_OBSERVATION) < evidence_trust(EvidenceSource.LLM_INFERENCE)
    ledger = EvidenceLedger(1)
    first = Evidence("e1", EvidenceSource.LOCAL_OBSERVATION, "current state", 1.0)
    second = Evidence("e2", EvidenceSource.LLM_INFERENCE, "hypothesis", 1.0)
    assert ledger.add(first) and not ledger.add(second)


def test_unknown_goal_read_only_resolution_never_calls_external_provider():
    registry = default_capability_registry()
    registry.bind_read_only("application_discovery", lambda goal: (Evidence("local", EvidenceSource.LOCAL_OBSERVATION, "resolved locally", 1.0, resolves_goal=True),))
    provider = FakeResearchProvider((Evidence("web", EvidenceSource.GENERAL_WEB, "external clue", 0.4),))
    outcome = ResearchOrchestrator(registry=registry, research_provider=provider).run_text("find the local application")
    assert outcome.result_code == "read_only_resolved" and outcome.session.state is ResearchState.SUCCEEDED
    assert provider.calls == 0


def test_state_change_stops_at_confirmation_and_cancel_has_zero_execution():
    goal = ResearchGoal.from_text("restart the selected service")
    provider = FakeReasoningProvider(action_plan(goal.goal_id))
    calls = []
    orchestrator = ResearchOrchestrator(reasoning_provider=provider, action_executor=lambda plan: calls.append(plan) or True, verifier=lambda *_: True)
    waiting = orchestrator.run(goal)
    assert waiting.session.state is ResearchState.WAITING_CONFIRMATION and calls == []
    cancelled = orchestrator.run(goal, confirm=False)
    assert cancelled.session.state is ResearchState.CANCELLED and calls == []


def test_approved_action_verifies_and_reflects():
    goal = ResearchGoal.from_text("restart the selected service")
    reflection = ReflectionPipeline()
    outcome = ResearchOrchestrator(reasoning_provider=FakeReasoningProvider(action_plan(goal.goal_id)), reflection=reflection, action_executor=lambda _: True, verifier=lambda *_: True).run(goal, confirm=True)
    assert outcome.result_code == "verified_success" and outcome.reflection is not None
    assert len(reflection.experiences) == 1


def test_verification_failure_replans_with_bound():
    goal = ResearchGoal.from_text("restart the selected service")
    provider = FakeReasoningProvider((action_plan(goal.goal_id, plan_id="first"), action_plan(goal.goal_id, plan_id="second")))
    checks = iter((False, True))
    outcome = ResearchOrchestrator(reasoning_provider=provider, action_executor=lambda _: True, verifier=lambda *_: next(checks), limits=ResearchLimits(max_replans=2)).run(goal, confirm=True)
    assert outcome.result_code == "verified_success" and outcome.plan.plan_id == "second" and outcome.execution_count == 2


def test_replan_limit_is_bounded():
    goal = ResearchGoal.from_text("restart the selected service")
    outcome = ResearchOrchestrator(reasoning_provider=FakeReasoningProvider(action_plan(goal.goal_id)), action_executor=lambda _: True, verifier=lambda *_: False, limits=ResearchLimits(max_replans=1)).run(goal, confirm=True)
    assert outcome.session.state is ResearchState.FAILED and outcome.execution_count == 2


def test_arbitrary_shell_and_download_plan_are_rejected_without_execution():
    goal = ResearchGoal.from_text("investigate an unknown task")
    bad = action_plan(goal.goal_id)
    bad_step = PlanStep("bad", PlanStepKind.ACTION_PROPOSAL, "service_control", "arbitrary_shell", "run arbitrary shell", SideEffect.SYSTEM_CHANGE.value, True, True)
    bad = CandidatePlan("bad", goal.goal_id, (bad_step,), "done", True, True, RiskClass.ADMIN, "verify")
    calls = []
    outcome = ResearchOrchestrator(reasoning_provider=FakeReasoningProvider(bad), action_executor=lambda _: calls.append(1) or True).run(goal, confirm=True)
    assert outcome.result_code == "candidate_rejected" and calls == []


def test_sensitive_evidence_is_not_sent_to_external_reasoning():
    goal = ResearchGoal.from_text("investigate an unknown task")
    sensitive = Evidence("e-sensitive", EvidenceSource.PERSONAL_MEMORY, "bounded safe marker", 1.0, sensitive=True)
    provider = FakeReasoningProvider(action_plan(goal.goal_id))
    ResearchOrchestrator(research_provider=FakeResearchProvider((sensitive,)), reasoning_provider=provider).run(goal)
    assert all(not item.sensitive for item in provider.last_evidence)


def test_model_routing_and_optional_llm_reflection_are_bounded():
    router = ModelRouter()
    assert router.choose(RoutingRequest("known", confidence=1.0)).tier is ModelTier.LOCAL
    assert router.choose(RoutingRequest("simple")).tier is ModelTier.LUNA
    assert router.choose(RoutingRequest("hard", hard_case=True, expected_quality_gain=.8, cost_budget=3, latency_budget=20)).tier is ModelTier.SOL
    provider = FakeLLMReflectionProvider(ReflectionEnrichment("structured enrichment", True))
    from windows_pet.reflection import Experience
    item = Experience("t", "research", "inspect", "safe target", "a", "b", "succeeded", "verified")
    assert OptionalLLMReflection(provider).enrich(item).reusable_signal and provider.calls == 1


def test_audit_events_are_structured_and_no_raw_target_is_written():
    sink = InMemoryAuditSink()
    goal = ResearchGoal.from_text("restart the selected service")
    ResearchOrchestrator(reasoning_provider=FakeReasoningProvider(action_plan(goal.goal_id)), audit=sink).run(goal)
    assert "restart the selected service" not in str(sink.events)
    assert any(event.event_type == "research_waiting_confirmation" for event in sink.events)


def test_ai_unknown_goal_handoff_returns_structured_local_result_without_execution():
    from windows_pet.ai_client import AIClient

    class Call:
        type = "function_call"
        call_id = "research-1"
        name = "research_unknown"
        arguments = '{"request":"inspect an unknown local issue"}'

    class Response:
        output = [Call()]
        output_text = ""

    class Final:
        output = []
        output_text = "調査結果を確認しました。"

    class Responses:
        def __init__(self): self.calls = 0
        def create(self, **kwargs):
            self.calls += 1
            return Response() if self.calls == 1 else Final()

    class Client:
        def __init__(self): self.responses = Responses()

    client = AIClient(client=Client(), api_key="test-key")
    assert client.stream_with_tools([], lambda _: None) == "調査結果を確認しました。"
    assert any(tool["name"] == "research_unknown" for tool in client._tools())


def test_research_planner_can_propose_file_rename_without_executing_it():
    from windows_pet.research.planner import CandidatePlanner
    from windows_pet.research.capabilities import default_capability_registry

    goal = ResearchGoal.from_text("make the selected file name clearer")
    plan = CandidatePlanner(default_capability_registry()).rename_file_resolution(goal, r"C:\Users\tester\old.txt", "report.txt")
    assert plan.requires_confirmation and plan.steps[0].capability_id == "file_rename"
