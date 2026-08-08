from __future__ import annotations

from dataclasses import dataclass

from .application_launch_request import ApplicationLaunchRequest
from .local_skill_store import LocalSkill, LocalSkillStore, normalize_skill_alias


_BUILT_IN_ALIASES = {
    "メモ帳を開いて": "メモ帳",
    "メモ帳を起動して": "メモ帳",
    "メモ帳開いて": "メモ帳",
    "notepadを開いて": "notepad",
    "notepad開いて": "notepad",
    "電卓を起動して": "電卓",
    "電卓を開いて": "電卓",
}


@dataclass(frozen=True)
class LocalSkillMatch:
    source: str
    skill: LocalSkill | None
    request: ApplicationLaunchRequest


class LocalSkillRouter:
    """Resolve only exact, code-owned or previously learned aliases."""

    def __init__(self, store: LocalSkillStore):
        self.store = store

    def route(self, text: str) -> LocalSkillMatch | None:
        normalized = normalize_skill_alias(text)
        target = _BUILT_IN_ALIASES.get(normalized)
        if target is not None:
            return LocalSkillMatch("built_in", None, ApplicationLaunchRequest(target, None, "local_skill", text))
        skill = self.store.find_alias(normalized)
        if skill is None or skill.intent != "launch_app":
            return None
        return LocalSkillMatch("learned", skill, ApplicationLaunchRequest(skill.target, None, "local_skill", text))
