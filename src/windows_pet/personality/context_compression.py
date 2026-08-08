from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundedContext:
    goal: str
    recent_task_context: tuple[str, ...]
    relevant_memories: tuple[dict[str, str], ...]
    relationship_style: dict[str, object]
    safe_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"goal": self.goal, "recent_task_context": list(self.recent_task_context), "relevant_memories": list(self.relevant_memories),
                "relationship_style": self.relationship_style, "safe_evidence": list(self.safe_evidence)}


class ContextCompressor:
    """Keeps the provider-facing context structured and bounded."""

    def __init__(self, *, max_goal_chars: int = 240, max_recent_items: int = 6, max_memory_items: int = 5, max_evidence_items: int = 8, max_item_chars: int = 320):
        self.max_goal_chars = max_goal_chars
        self.max_recent_items = max_recent_items
        self.max_memory_items = max_memory_items
        self.max_evidence_items = max_evidence_items
        self.max_item_chars = max_item_chars

    def compress(self, *, goal: str, recent_task_context: list[str] | tuple[str, ...] = (), relevant_memories: list[dict[str, str]] | tuple[dict[str, str], ...] = (), relationship_style: dict[str, object] | None = None, safe_evidence: list[str] | tuple[str, ...] = ()) -> BoundedContext:
        def bounded(value: object) -> str:
            return str(value).replace("\x00", "")[: self.max_item_chars]
        safe_memories = tuple({str(key)[:80]: bounded(value) for key, value in item.items() if str(key)[:80] and value is not None} for item in relevant_memories[:self.max_memory_items])
        return BoundedContext(
            goal=str(goal).strip()[: self.max_goal_chars],
            recent_task_context=tuple(bounded(item) for item in recent_task_context[:self.max_recent_items]),
            relevant_memories=safe_memories,
            relationship_style=dict(relationship_style or {}),
            safe_evidence=tuple(bounded(item) for item in safe_evidence[:self.max_evidence_items]),
        )
