from __future__ import annotations
import ntpath
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath
from .action_models import ActionProposal, ActionTarget, ActionPreview, SimpleActionPreview, SideEffect, ConfirmationType, ToolContract
from .execution_grant import ExecutionGrantStore, GrantResultCode
from .policy_gate import PolicyGate


class LaunchValidationCode(str, Enum):
    OK = "ok"
    MISSING_PATH = "missing_path"
    RELATIVE_PATH = "relative_path"
    NETWORK_PATH = "network_path"
    DEVICE_PATH = "device_path"
    URL_PATH = "url_path"
    UNEXPANDED_ENVIRONMENT = "unexpanded_environment"
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    NOT_FOUND = "not_found"
    NOT_FILE = "not_file"
    RESOLVE_FAILED = "resolve_failed"
    STAT_FAILED = "stat_failed"
    IDENTITY_CHANGED = "identity_changed"


@dataclass(frozen=True)
class ApplicationLaunchTarget:
    display_name: str
    canonical_path: str
    file_size: int
    modified_time_ns: int


class ApplicationLaunchValidator:
    """Validates local, regular .exe files without resolving or executing links."""
    def validate(self, candidate) -> tuple[ApplicationLaunchTarget | None, LaunchValidationCode]:
        raw = str(getattr(candidate, "executable_path", ""))
        if not raw: return None, LaunchValidationCode.MISSING_PATH
        if "://" in raw: return None, LaunchValidationCode.URL_PATH
        if raw.startswith("\\\\.\\") or raw.startswith("\\\\?\\"): return None, LaunchValidationCode.DEVICE_PATH
        if raw.startswith("\\\\"): return None, LaunchValidationCode.NETWORK_PATH
        if "%" in raw: return None, LaunchValidationCode.UNEXPANDED_ENVIRONMENT
        if not ntpath.isabs(raw): return None, LaunchValidationCode.RELATIVE_PATH
        if ntpath.splitext(raw)[1].casefold() != ".exe": return None, LaunchValidationCode.UNSUPPORTED_EXTENSION
        try:
            path = Path(raw).resolve(strict=True)
            if ntpath.splitext(str(path))[1].casefold() != ".exe": return None, LaunchValidationCode.UNSUPPORTED_EXTENSION
            stat = path.stat()
            if not path.is_file(): return None, LaunchValidationCode.NOT_FILE
            return ApplicationLaunchTarget(getattr(candidate, "display_name", ""), str(path), stat.st_size, stat.st_mtime_ns), LaunchValidationCode.OK
        except FileNotFoundError: return None, LaunchValidationCode.NOT_FOUND
        except OSError: return None, LaunchValidationCode.RESOLVE_FAILED

    def matches(self, target: ApplicationLaunchTarget) -> bool:
        current, code = self.validate(type("Candidate", (), {"executable_path": target.canonical_path, "display_name": target.display_name})())
        return code is LaunchValidationCode.OK and current == target


APPLICATION_LAUNCH_CONTRACT = ToolContract("application_launcher", "1", "launch_application", SideEffect.APPLICATION_LAUNCH, ConfirmationType.SIMPLE, False, False, True, 10.0, "process creation and short post-launch verification", ("status", "result_code", "verification_result"))


class ApplicationLaunchStatus(str, Enum):
    STARTED = "started"; HANDED_OFF = "handed_off"; REJECTED = "rejected"; FAILED = "failed"; CANCELLED = "cancelled"


@dataclass(frozen=True)
class ApplicationLaunchOutcome:
    status: ApplicationLaunchStatus
    result_code: str


class ApplicationLaunchExecutor:
    """Consumes a grant, revalidates the target, and starts only one bare .exe."""
    def __init__(self, grants: ExecutionGrantStore, validator=None, process_factory=None):
        self.grants = grants
        self.validator = validator or ApplicationLaunchValidator()
        self.process_factory = process_factory or subprocess.Popen

    def execute(self, grant_id: str, proposal: ActionProposal, target: ApplicationLaunchTarget) -> ApplicationLaunchOutcome:
        result = self.grants.consume_for(grant_id, APPLICATION_LAUNCH_CONTRACT, proposal)
        if not result.success: return ApplicationLaunchOutcome(ApplicationLaunchStatus.REJECTED, result.reason.value)
        if not self.validator.matches(target): return ApplicationLaunchOutcome(ApplicationLaunchStatus.REJECTED, LaunchValidationCode.IDENTITY_CHANGED.value)
        try:
            process = self.process_factory([target.canonical_path], shell=False, cwd=str(Path(target.canonical_path).parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            code = process.poll()
            return ApplicationLaunchOutcome(ApplicationLaunchStatus.HANDED_OFF if code == 0 else ApplicationLaunchStatus.STARTED, "process_created")
        except OSError: return ApplicationLaunchOutcome(ApplicationLaunchStatus.FAILED, "process_create_failed")
