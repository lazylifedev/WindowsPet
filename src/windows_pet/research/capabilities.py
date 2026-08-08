from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..action_models import SideEffect
from .models import Evidence, EvidenceSource, ResearchGoal


@dataclass(frozen=True)
class Capability:
    capability_id: str
    operations: tuple[str, ...]
    side_effect: SideEffect
    requires_confirmation: bool
    requires_admin: bool
    read_only: bool
    verification_support: tuple[str, ...] = ()


class CapabilityRegistry:
    """Code-owned capability catalog; callers cannot alter effect metadata per request."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._capabilities = {item.capability_id: item for item in capabilities}
        self._readers: dict[str, Callable[[ResearchGoal], Iterable[Evidence]]] = {}

    def register(self, capability: Capability) -> None:
        if capability.capability_id in self._capabilities:
            raise ValueError("duplicate_capability")
        if capability.read_only and capability.side_effect is not SideEffect.READ_ONLY:
            raise ValueError("read_only_capability_has_side_effect")
        self._capabilities[capability.capability_id] = capability

    def bind_read_only(self, capability_id: str, reader: Callable[[ResearchGoal], Iterable[Evidence]]) -> None:
        capability = self.get(capability_id)
        if not capability.read_only:
            raise ValueError("reader_must_be_read_only")
        self._readers[capability_id] = reader

    def get(self, capability_id: str) -> Capability:
        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise KeyError("unknown_capability") from exc

    def all(self) -> tuple[Capability, ...]:
        return tuple(self._capabilities[key] for key in sorted(self._capabilities))

    def summary(self) -> tuple[str, ...]:
        return tuple(f"{item.capability_id}:{','.join(item.operations)}" for item in self.all())

    def inspect(self, goal: ResearchGoal) -> tuple[Evidence, ...]:
        evidence: list[Evidence] = []
        for capability_id, reader in tuple(self._readers.items()):
            capability = self._capabilities[capability_id]
            if not capability.read_only:
                continue
            for item in tuple(reader(goal)):
                if item.source not in {EvidenceSource.LOCAL_OBSERVATION, EvidenceSource.BUILT_IN}:
                    raise ValueError("reader_produced_untrusted_source")
                evidence.append(item)
        return tuple(evidence)


def default_capability_registry() -> CapabilityRegistry:
    readonly = (
        Capability("application_discovery", ("discover_applications",), SideEffect.READ_ONLY, False, False, True, ("schema",)),
        Capability("process_inspection", ("inspect_processes",), SideEffect.READ_ONLY, False, False, True, ("process_absent",)),
        Capability("service_inspection", ("inspect_services",), SideEffect.READ_ONLY, False, False, True, ("service_state",)),
        Capability("network_read", ("inspect_network",), SideEffect.READ_ONLY, False, False, True, ("schema",)),
        Capability("event_log_read", ("inspect_event_logs",), SideEffect.READ_ONLY, False, False, True, ("schema",)),
        Capability("registry_catalog_read", ("inspect_fixed_registry_catalog",), SideEffect.READ_ONLY, False, False, True, ("schema",)),
        Capability("winget_search", ("search_packages",), SideEffect.READ_ONLY, False, False, True, ("package_metadata",)),
        Capability("file_search", ("search_file_metadata",), SideEffect.READ_ONLY, False, False, True, ("schema",)),
        Capability("local_skills", ("match_local_skill",), SideEffect.READ_ONLY, False, False, True, ("exact_match",)),
        Capability("personal_memory", ("lookup_bounded_memory",), SideEffect.READ_ONLY, False, False, True, ("privacy_filter",)),
        Capability("reflection", ("reflect_verified_result",), SideEffect.READ_ONLY, False, False, True, ("verified_only",)),
        Capability("elevation_availability", ("inspect_elevation",), SideEffect.READ_ONLY, False, False, True, ("schema",)),
    )
    registry = CapabilityRegistry(readonly)
    registry.register(Capability("process_control", ("stop_process",), SideEffect.PROCESS_CONTROL, True, False, False, ("process_absent",)))
    registry.register(Capability("service_control", ("restart_service",), SideEffect.SYSTEM_CHANGE, True, True, False, ("service_state",)))
    return registry
