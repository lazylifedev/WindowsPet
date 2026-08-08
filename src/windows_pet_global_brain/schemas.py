from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CompatibilityFacts(StrictModel):
    windows_version: str | None = Field(default=None, max_length=80)
    architecture: str | None = Field(default=None, max_length=40)
    application_version: str | None = Field(default=None, max_length=80)
    capability_version: str | None = Field(default=None, max_length=80)

    @field_validator("windows_version", "architecture", "application_version", "capability_version")
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None


class LookupRequest(StrictModel):
    intent: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=160)
    compatibility: CompatibilityFacts
    client_knowledge_version: str | None = Field(default=None, max_length=40)


class SkillPayload(StrictModel):
    intent: str = Field(min_length=1, max_length=80)
    target_type: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(min_length=1, max_length=20)
    compatibility: CompatibilityFacts

    @field_validator("aliases")
    @classmethod
    def bounded_aliases(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 120 for value in values):
            raise ValueError("invalid_alias")
        return values


class CandidateRequest(StrictModel):
    candidate_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    installation_evidence_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    verified_success: bool
    skill: SkillPayload


class ResultRequest(StrictModel):
    event_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    knowledge_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    knowledge_version: str = Field(min_length=1, max_length=40, pattern=r"^v[0-9]+$")
    installation_evidence_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    compatibility: CompatibilityFacts
    verified_success: bool
    failure_category: str | None = Field(default=None, max_length=40)

    @field_validator("failure_category")
    @classmethod
    def safe_failure_category(cls, value: str | None) -> str | None:
        allowed = {"timeout", "incompatible", "not_found", "permission", "verification_failed", "unknown"}
        if value is not None and value not in allowed:
            raise ValueError("unsupported_failure_category")
        return value


class SkillResponse(StrictModel):
    knowledge_id: str
    knowledge_version: str
    trust_state: str
    intent: str
    target_type: str
    target: str
    aliases: list[str]
    compatibility: CompatibilityFacts
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class LookupResponse(StrictModel):
    knowledge_version: str
    stale_client: bool
    matches: list[SkillResponse]


class CandidateResponse(StrictModel):
    accepted: bool
    reason: str
    candidate_id: str | None = None
    knowledge_id: str | None = None
    trust_state: str | None = None
    duplicate: bool = False


class ResultResponse(StrictModel):
    accepted: bool
    reason: str
    trust_state: str | None = None
    promoted: bool = False
    success_count: int = 0
    failure_count: int = 0
    distinct_installations: int = 0
    duplicate: bool = False


class HealthResponse(StrictModel):
    status: str
    service: str
    knowledge_version: str


class VersionResponse(StrictModel):
    knowledge_version: str
