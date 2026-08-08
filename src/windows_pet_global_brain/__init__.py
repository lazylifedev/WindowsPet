"""Local Global Brain service boundary.

The package contains domain and API code only.  Cloud credentials and concrete
Firestore SDK imports deliberately stay outside the business logic.
"""

from .domain import (
    CompatibilityConstraint,
    ExecutionEvidenceAggregate,
    KnowledgeCandidate,
    KnowledgeVersion,
    PromotionDecision,
    SharedAlias,
    SharedSkill,
    TrustState,
)
from .repositories import (
    FirestoreGlobalBrainRepository,
    InMemoryGlobalBrainRepository,
)
from .service import GlobalBrainService, PromotionPolicy


def create_app(*args, **kwargs):
    """Load the optional FastAPI surface without coupling domain imports to it."""
    from .api import create_app as factory

    return factory(*args, **kwargs)

__all__ = [
    "CompatibilityConstraint",
    "ExecutionEvidenceAggregate",
    "FirestoreGlobalBrainRepository",
    "GlobalBrainService",
    "InMemoryGlobalBrainRepository",
    "KnowledgeCandidate",
    "KnowledgeVersion",
    "PromotionDecision",
    "PromotionPolicy",
    "SharedAlias",
    "SharedSkill",
    "TrustState",
    "create_app",
]
