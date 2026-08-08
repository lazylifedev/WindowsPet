from __future__ import annotations

import os
import secrets
import hashlib
from dataclasses import dataclass
from typing import Callable, Mapping

from .action_models import ActionProposal, ActionProposalFactory, ActionTarget, ConfirmationType, SideEffect, SimpleActionPreview, ToolContract
from .audit_log import AuditEvent, NullAuditSink
from .execution_grant import ExecutionGrantStore


WINDOWS_SETTINGS = {
    "bluetooth": "ms-settings:bluetooth",
    "network": "ms-settings:network",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "apps": "ms-settings:appsfeatures",
    "windows_update": "ms-settings:windowsupdate",
}


def canonical_script(script: str) -> bytes:
    if not isinstance(script, str) or "\x00" in script or any(ord(char) < 32 and char not in "\n\t" for char in script):
        raise ValueError("invalid_script")
    return (script.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")


def script_sha256(script: str) -> str:
    return hashlib.sha256(canonical_script(script)).hexdigest()


@dataclass(frozen=True)
class PowerShellOperationTemplate:
    template_id: str
    operation_id: str
    effect: SideEffect
    requires_admin: bool
    parameter_names: tuple[str, ...]
    script_factory: Callable[[Mapping[str, str]], str]
    verification_factory: Callable[[Mapping[str, str]], str]
    audit_fields: tuple[str, ...] = ()


class PowerShellTemplateRegistry:
    """Code-owned templates; callers can select IDs and strict parameters only."""

    def __init__(self, templates=()):
        self._templates = {}
        for template in templates:
            self.register(template)

    def register(self, template: PowerShellOperationTemplate) -> None:
        if template.template_id in self._templates or not template.template_id.strip() or not template.operation_id.strip():
            raise ValueError("invalid_or_duplicate_template")
        if not template.parameter_names or len(set(template.parameter_names)) != len(template.parameter_names):
            raise ValueError("invalid_template_parameters")
        if template.effect is SideEffect.READ_ONLY and template.requires_admin:
            raise ValueError("readonly_requires_admin")
        self._templates[template.template_id] = template

    def get(self, template_id: str) -> PowerShellOperationTemplate:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise KeyError("unknown_template") from exc

    def all(self) -> tuple[PowerShellOperationTemplate, ...]:
        return tuple(self._templates[key] for key in sorted(self._templates))


def default_template_registry() -> PowerShellTemplateRegistry:
    def settings_script(params):
        # Code-owned URI catalog, never caller-provided script or URI.
        return f"Start-Process -FilePath '{WINDOWS_SETTINGS[params['settings_id']]}'"

    def settings_verify(params):
        return "foreground settings URI is selected catalog entry"

    return PowerShellTemplateRegistry((PowerShellOperationTemplate(
        "windows_pet.open_windows_settings.v1", "open_windows_settings", SideEffect.APPLICATION_LAUNCH,
        False, ("settings_id",), settings_script, settings_verify, ("settings_id", "uri", "script_sha256", "verification_result")),))


@dataclass(frozen=True)
class StructuredOperationProposal:
    operation_id: str
    template_id: str
    parameters: tuple[tuple[str, str], ...]
    script_text: str
    script_sha256: str
    verification_plan: str


class StructuredOperationFactory:
    def __init__(self, registry=None, proposal_factory=None):
        self.registry = registry or default_template_registry()
        self.proposal_factory = proposal_factory or ActionProposalFactory()

    def create(self, task_id: str, operation_id: str, parameters: Mapping[str, str]) -> tuple[ActionProposal, StructuredOperationProposal]:
        if not isinstance(parameters, Mapping):
            raise ValueError("invalid_parameters")
        matches = [template for template in self.registry.all() if template.operation_id == operation_id]
        if len(matches) != 1:
            raise ValueError("unknown_operation")
        template = matches[0]
        if set(parameters) != set(template.parameter_names) or any(not isinstance(value, str) or not value.strip() for value in parameters.values()):
            raise ValueError("invalid_parameters")
        if operation_id == "open_windows_settings" and parameters["settings_id"] not in WINDOWS_SETTINGS:
            raise ValueError("unsupported_settings_id")
        fixed = {key: str(parameters[key]) for key in template.parameter_names}
        script = template.script_factory(fixed)
        verification = template.verification_factory(fixed)
        if not isinstance(script, str) or not script.strip() or not isinstance(verification, str) or not verification.strip():
            raise ValueError("invalid_template_output")
        operation = StructuredOperationProposal(operation_id, template.template_id, tuple((key, fixed[key]) for key in template.parameter_names), script, script_sha256(script), verification)
        contract = ToolContract("structured_operation", "1", operation_id, template.effect, ConfirmationType.SIMPLE,
                                True, template.requires_admin, True, 10.0, verification, template.audit_fields)
        preview = SimpleActionPreview("Windows設定を開く", f"指定されたWindows設定画面を開きます。対象: {fixed['settings_id']}", "開く")
        proposal = self.proposal_factory.create(contract, task_id, ActionTarget("windows_settings", fixed["settings_id"], f"Windows設定: {fixed['settings_id']}"),
                                                {"template_id": template.template_id, "operation_id": operation_id, "settings_id": fixed["settings_id"], "uri": WINDOWS_SETTINGS[fixed["settings_id"]], "script": script, "script_sha256": script_sha256(script), "backend": "windows_powershell", "verification": verification}, preview)
        return proposal, operation


@dataclass(frozen=True)
class StructuredOperationOutcome:
    success: bool
    result_code: str
    verification_result: str = ""


SETTINGS_OPERATION_CONTRACT = ToolContract("structured_operation", "1", "open_windows_settings", SideEffect.APPLICATION_LAUNCH, ConfirmationType.SIMPLE, True, False, True, 10.0, "foreground settings URI is selected catalog entry", ("settings_id", "uri", "script_sha256", "verification_result"))


class OpenWindowsSettingsExecutor:
    def __init__(self, grants: ExecutionGrantStore, opener: Callable[[str], object] | None = None,
                 verifier: Callable[[str], bool] | None = None, audit=None):
        self.grants = grants
        self.opener = opener or (lambda uri: os.startfile(uri))
        self.verifier = verifier or (lambda _: True)
        self.audit = audit or NullAuditSink()

    def execute(self, grant_id: str, proposal: ActionProposal, operation: StructuredOperationProposal) -> StructuredOperationOutcome:
        params = proposal.parameters
        if (proposal.operation != "open_windows_settings" or proposal.target.kind != "windows_settings" or
                not isinstance(params, Mapping) or params.get("template_id") != operation.template_id or
                params.get("operation_id") != operation.operation_id or params.get("settings_id") not in WINDOWS_SETTINGS or
                params.get("uri") != WINDOWS_SETTINGS[params.get("settings_id")]):
            return StructuredOperationOutcome(False, "invalid_request")
        if params.get("script_sha256") != operation.script_sha256 or script_sha256(operation.script_text) != operation.script_sha256:
            return StructuredOperationOutcome(False, "script_hash_mismatch")
        consumed = self.grants.consume_for(grant_id, SETTINGS_OPERATION_CONTRACT, proposal)
        if not consumed.success:
            return StructuredOperationOutcome(False, consumed.reason.value)
        uri = WINDOWS_SETTINGS[params["settings_id"]]
        self.audit.write(AuditEvent("execution_started", result_code="started", proposal_id=proposal.proposal_id, grant_id=grant_id, operation=proposal.operation, side_effect=proposal.side_effect.value, confirmation_type=proposal.confirmation_type.value))
        try:
            self.opener(uri)
            verified = bool(self.verifier(uri))
        except (OSError, ValueError):
            verified = False
        if not verified:
            self.audit.write(AuditEvent("verification_failed", result_code="verification_failed", proposal_id=proposal.proposal_id, grant_id=grant_id, operation=proposal.operation, side_effect=proposal.side_effect.value, confirmation_type=proposal.confirmation_type.value))
            return StructuredOperationOutcome(False, "verification_failed", "settings_target_not_verified")
        self.audit.write(AuditEvent("verification_succeeded", result_code="verified", proposal_id=proposal.proposal_id, grant_id=grant_id, operation=proposal.operation, side_effect=proposal.side_effect.value, confirmation_type=proposal.confirmation_type.value, verification_result="catalog_uri_accepted"))
        return StructuredOperationOutcome(True, "verified", "catalog_uri_accepted")
