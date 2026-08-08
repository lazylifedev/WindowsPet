"""Local-only shared-knowledge abstraction; no cloud transport is connected."""

from .cache import SharedKnowledgeCache, SharedKnowledgeMatch
from .client import GlobalBrainClient, LocalOnlyGlobalBrainClient
from .models import ShareDecision, SharedSkillRecord
from .repository import InMemorySharedKnowledgeRepository, SQLiteSharedKnowledgeRepository
from .sanitizer import SharedKnowledgeSanitizer

__all__ = [
    "GlobalBrainClient",
    "InMemorySharedKnowledgeRepository",
    "LocalOnlyGlobalBrainClient",
    "SQLiteSharedKnowledgeRepository",
    "ShareDecision",
    "SharedKnowledgeCache",
    "SharedKnowledgeMatch",
    "SharedKnowledgeSanitizer",
    "SharedSkillRecord",
]
