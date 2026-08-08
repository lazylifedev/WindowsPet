from __future__ import annotations

from fastapi import FastAPI

from .domain import CompatibilityConstraint
from .repositories import InMemoryGlobalBrainRepository
from .schemas import (
    CandidateRequest,
    CandidateResponse,
    HealthResponse,
    LookupRequest,
    LookupResponse,
    ResultRequest,
    ResultResponse,
    SkillResponse,
    VersionResponse,
)
from .service import GlobalBrainService


def create_app(service: GlobalBrainService | None = None) -> FastAPI:
    app = FastAPI(title="WindowsPet Global Brain", version="0.1.0")
    brain = service or GlobalBrainService(InMemoryGlobalBrainRepository())

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse("ok", "global_brain", brain.repository.current_version().version)

    @app.get("/v1/knowledge/version", response_model=VersionResponse)
    def knowledge_version() -> VersionResponse:
        return VersionResponse(brain.repository.current_version().version)

    @app.post("/v1/knowledge/lookup", response_model=LookupResponse)
    def lookup(request: LookupRequest) -> LookupResponse:
        result = brain.lookup(
            intent=request.intent,
            target=request.target,
            compatibility=CompatibilityConstraint(**request.compatibility.model_dump()),
            client_knowledge_version=request.client_knowledge_version,
        )
        return LookupResponse(
            knowledge_version=result.server_version,
            stale_client=result.stale_client,
            matches=[_skill_response(skill) for skill in result.matches],
        )

    @app.post("/v1/candidates", response_model=CandidateResponse)
    def candidates(request: CandidateRequest) -> CandidateResponse:
        skill = request.skill.model_dump()
        skill["compatibility"] = [
            f"{key}={value}"
            for key, value in skill["compatibility"].items()
            if value
        ]
        result = brain.submit_candidate(
            candidate_id=request.candidate_id,
            installation_evidence_id=request.installation_evidence_id,
            skill_data=skill,
            verified_success=request.verified_success,
        )
        return CandidateResponse(
            accepted=result.accepted,
            reason=result.reason,
            candidate_id=result.candidate.candidate_id if result.candidate else None,
            knowledge_id=result.candidate.skill.knowledge_id if result.candidate else None,
            trust_state=result.candidate.skill.trust_state.value if result.candidate else None,
            duplicate=result.duplicate,
        )

    @app.post("/v1/results", response_model=ResultResponse)
    def results(request: ResultRequest) -> ResultResponse:
        result = brain.submit_result(
            event_id=request.event_id,
            knowledge_id=request.knowledge_id,
            knowledge_version=request.knowledge_version,
            installation_evidence_id=request.installation_evidence_id,
            compatibility=CompatibilityConstraint(**request.compatibility.model_dump()),
            verified_success=request.verified_success,
            failure_category=request.failure_category,
        )
        aggregate = result.aggregate
        return ResultResponse(
            accepted=result.accepted,
            reason=result.reason,
            trust_state=result.promotion.trust_state.value if result.promotion else None,
            promoted=result.promotion.promoted if result.promotion else False,
            success_count=aggregate.success_count if aggregate else 0,
            failure_count=aggregate.failure_count if aggregate else 0,
            distinct_installations=aggregate.distinct_installations if aggregate else 0,
            duplicate=result.duplicate,
        )

    return app


def _skill_response(skill) -> SkillResponse:
    return SkillResponse(
        knowledge_id=skill.knowledge_id,
        knowledge_version=skill.knowledge_version,
        trust_state=skill.trust_state.value,
        intent=skill.intent,
        target_type=skill.target_type,
        target=skill.target,
        aliases=[alias.value for alias in skill.aliases],
        compatibility=skill.compatibility.__dict__,
        success_count=skill.success_count,
        failure_count=skill.failure_count,
        confidence=skill.confidence,
    )
