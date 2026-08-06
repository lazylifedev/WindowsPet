from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SideEffect(str, Enum):
    READ_ONLY = "read_only"


class ConfirmationType(str, Enum):
    NONE = "none"


class InspectionErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    TIMEOUT = "timeout"
    INVALID_DATA = "invalid_data"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    UNEXPECTED_ERROR = "unexpected_error"


@dataclass(frozen=True)
class ToolContract:
    name: str = "local_inspection"
    version: str = "1"
    operation: str = "inspect_local_pc"
    side_effect: SideEffect = SideEffect.READ_ONLY
    reversible: bool = True
    requires_admin: bool = False
    confirmation: ConfirmationType = ConfirmationType.NONE
    timeout_seconds: float = 5.0
    cancellation_supported: bool = True
    verification_method: str = "structured local inspection result"
    audit_fields: tuple[str, ...] = ("started_at", "completed_at", "status", "partial_error_count")


@dataclass(frozen=True)
class SystemInfo:
    os_name: str
    version: str
    build: str
    architecture: str
    computer_name: str
    username: str
    is_admin: bool
    python_architecture: str


@dataclass(frozen=True)
class PathInspection:
    item_count: int
    existing_count: int
    missing_count: int
    normalized_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppCandidate:
    display_name: str
    version: str = ""
    publisher: str = ""
    source: str = ""
    executable_name: str = ""
    executable_path: str = ""
    executable_exists: bool | None = None


@dataclass(frozen=True)
class WingetStatus:
    available: bool
    version: str = ""
    error: InspectionErrorCode | None = None


@dataclass(frozen=True)
class PartialError:
    area: str
    code: InspectionErrorCode


@dataclass
class InspectionSnapshot:
    system: SystemInfo
    path: PathInspection
    winget: WingetStatus
    app_paths: list[AppCandidate] = field(default_factory=list)
    start_menu: list[AppCandidate] = field(default_factory=list)
    installed_apps: list[AppCandidate] = field(default_factory=list)
    partial_errors: list[PartialError] = field(default_factory=list)
    searched_candidates: list[AppCandidate] = field(default_factory=list)
    inspected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    contract: ToolContract = field(default_factory=ToolContract)
