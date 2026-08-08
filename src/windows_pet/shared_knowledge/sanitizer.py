from __future__ import annotations

import ipaddress
import re
import secrets
from collections.abc import Mapping

from .models import ShareDecision, SharedSkillRecord

_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PATH = re.compile(r"(?:^[A-Za-z]:[\\/]|^\\\\|/Users/|/home/|/var/|\\Users\\|private-tool)", re.I)
_SECRET = re.compile(r"(?:api[_ -]?key|access[_ -]?token|password|credential|secret|bearer|private key)", re.I)
_IP = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{1,3}){3})(?!\d)")
_FORBIDDEN_FIELDS = {"username", "personal_path", "private_filename", "email", "credential", "private_ip", "hostname", "conversation", "schedule", "preference", "habit", "memory", "screenshot", "document", "raw_log", "stdout", "stderr"}


def _sensitive(value: object) -> bool:
    text = str(value)
    if _EMAIL.search(text) or _PATH.search(text) or _SECRET.search(text):
        return True
    for match in _IP.findall(text):
        try:
            if ipaddress.ip_address(match).is_private:
                return True
        except ValueError:
            return True
    return False


class SharedKnowledgeSanitizer:
    """Rejects personal/raw material before it can enter a shared candidate."""

    def sanitize(self, candidate: Mapping[str, object] | SharedSkillRecord) -> ShareDecision:
        if isinstance(candidate, SharedSkillRecord):
            data = {"intent": candidate.intent, "target_type": candidate.target_type, "target": candidate.target,
                    "aliases": candidate.aliases, "success_count": candidate.success_count, "failure_count": candidate.failure_count,
                    "confidence": candidate.confidence, "source": candidate.source, "compatibility": candidate.compatibility,
                    "trusted": candidate.trusted, "updated_at": candidate.updated_at, "expires_at": candidate.expires_at, "record_id": candidate.record_id}
        else:
            data = dict(candidate)
        forbidden = {str(key).casefold() for key in data} & _FORBIDDEN_FIELDS
        if forbidden:
            return ShareDecision(False, f"personal_or_raw_field:{sorted(forbidden)[0]}")
        for key in ("intent", "target_type", "target", "source", "compatibility"):
            if _sensitive(data.get(key, "")):
                return ShareDecision(False, f"sensitive_{key}")
        aliases = tuple(str(alias).strip()[:120] for alias in data.get("aliases", ()) if str(alias).strip())
        if not aliases or len(aliases) > 20 or any(_sensitive(alias) for alias in aliases):
            return ShareDecision(False, "unsafe_aliases")
        intent, target_type, target = str(data.get("intent", "")).strip(), str(data.get("target_type", "")).strip(), str(data.get("target", "")).strip()
        if not intent or not target_type or not target or len(intent) > 80 or len(target_type) > 80 or len(target) > 160:
            return ShareDecision(False, "incomplete_or_oversize")
        if any(token in f"{intent} {target_type}".casefold() for token in ("habit", "preference", "schedule", "conversation", "memory")):
            return ShareDecision(False, "personal_domain")
        try:
            record = SharedSkillRecord(
                record_id=str(data.get("record_id") or f"shared-{secrets.token_hex(8)}"), intent=intent, target_type=target_type, target=target,
                aliases=aliases, success_count=max(0, int(data.get("success_count", 0))), failure_count=max(0, int(data.get("failure_count", 0))),
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))), source="sanitized_local_verified",
                compatibility=tuple(str(item)[:80] for item in data.get("compatibility", ()) if str(item).strip()), trusted=bool(data.get("trusted", False)),
                updated_at=str(data.get("updated_at") or SharedSkillRecord.__dataclass_fields__["updated_at"].default_factory()), expires_at=data.get("expires_at"),
            )
        except (TypeError, ValueError):
            return ShareDecision(False, "invalid_record")
        return ShareDecision(True, "eligible_abstract_skill", record)

    def is_eligible(self, candidate: Mapping[str, object] | SharedSkillRecord) -> bool:
        return self.sanitize(candidate).eligible
