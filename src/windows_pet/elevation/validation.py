from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import unicodedata

from ..service_restart import (
    PROTECTED_SERVICE_NAMES,
    RESTART_SERVICE_SCRIPT,
    RESTART_SERVICE_TEMPLATE_ID,
    canonical_script,
    script_sha256,
)
from .envelope import parameter_digest
from .models import ElevationEnvelope, ElevationReason


class BrokerValidationError(ValueError):
    def __init__(self, reason: str | ElevationReason):
        self.reason = reason.value if isinstance(reason, ElevationReason) else str(reason)
        super().__init__(self.reason)


@dataclass(frozen=True)
class BrokerFileIdentity:
    path: Path
    filename: str
    size: int
    inode: int
    device: int
    sha256: str


class AuthenticodeVerifier(Protocol):
    def verify(self, path: Path) -> bool | None: ...


class UnconfiguredAuthenticodeVerifier:
    """Future hook; ``None`` means signing is not configured, never success."""
    def verify(self, path: Path) -> bool | None:
        return None


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _reject_special_path(path: Path) -> None:
    raw = os.fspath(path)
    if raw.startswith(("\\\\", "\\\\?\\", "\\\\.\\")):
        raise BrokerValidationError(ElevationReason.BROKER_IDENTITY_INVALID)


def validate_broker_identity(path: Path, *, expected_path: Path | None = None,
                             expected_filename: str = "WindowsPet.ElevationBroker.exe",
                             install_root: Path | None = None,
                             authenticode: AuthenticodeVerifier | None = None) -> BrokerFileIdentity:
    """Validate the local helper before any elevation API is called."""
    path = Path(path)
    _reject_special_path(path)
    if not path.is_absolute() or path.name != expected_filename or not path.is_file() or _is_reparse(path):
        raise BrokerValidationError(ElevationReason.BROKER_IDENTITY_INVALID)
    resolved = path.resolve()
    if expected_path is not None and resolved != Path(expected_path).resolve():
        raise BrokerValidationError(ElevationReason.BROKER_IDENTITY_INVALID)
    if install_root is not None:
        root = Path(install_root).resolve()
        if not resolved.is_relative_to(root):
            raise BrokerValidationError(ElevationReason.BROKER_IDENTITY_INVALID)
    try:
        stat_result = resolved.stat()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as error:
        raise BrokerValidationError(ElevationReason.BROKER_IDENTITY_INVALID) from error
    if authenticode is not None and authenticode.verify(resolved) is False:
        raise BrokerValidationError(ElevationReason.BROKER_IDENTITY_INVALID)
    return BrokerFileIdentity(resolved, resolved.name, stat_result.st_size,
                              getattr(stat_result, "st_ino", 0),
                              getattr(stat_result, "st_dev", 0), digest)


def _safe_text(value: object, *, max_length: int = 260) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or any(ord(char) < 32 for char in value):
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    if value != unicodedata.normalize("NFKC", value) or value != value.strip():
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    return value


def validate_restart_service_parameters(parameters: object) -> str:
    if not isinstance(parameters, dict):
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    expected = {"service_name", "display_name", "observed_status", "script_sha256",
                "template_version", "backend", "environment_keys"}
    if set(parameters) != expected:
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    service_name = _safe_text(parameters["service_name"])
    _safe_text(parameters["display_name"])
    _safe_text(parameters["observed_status"], max_length=64)
    _safe_text(parameters["script_sha256"], max_length=64)
    if not re.fullmatch(r"[0-9a-f]{64}", parameters["script_sha256"]):
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    if parameters["template_version"] != RESTART_SERVICE_TEMPLATE_ID or parameters["backend"] != "windows_powershell":
        raise BrokerValidationError(ElevationReason.TEMPLATE_MISMATCH)
    if tuple(parameters["environment_keys"]) != (
            "WINDOWSPET_PS_PARAMETERS", "SystemRoot", "WINDIR", "SystemDrive",
            "ComSpec", "TEMP", "TMP", "PSModulePath", "PATHEXT"):
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    if unicodedata.normalize("NFKC", service_name).casefold() in {unicodedata.normalize("NFKC", value).casefold() for value in PROTECTED_SERVICE_NAMES}:
        raise BrokerValidationError(ElevationReason.INVALID_PARAMETERS)
    return service_name


class EnvelopeValidator:
    """Broker-side fail-closed validation and exact-template reconstruction."""
    allowed_operations = frozenset({"restart_service"})

    def __init__(self, *, now=None):
        self.now = now

    def validate(self, envelope: ElevationEnvelope) -> str:
        if not isinstance(envelope, ElevationEnvelope):
            raise BrokerValidationError(ElevationReason.INVALID_ENVELOPE)
        current = self.now() if self.now else __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        if current >= envelope.expires_at:
            raise BrokerValidationError(ElevationReason.EXPIRED_ENVELOPE)
        if envelope.operation_id not in self.allowed_operations:
            raise BrokerValidationError(ElevationReason.WRONG_OPERATION)
        if envelope.effect_class != "system_change":
            raise BrokerValidationError(ElevationReason.WRONG_EFFECT_CLASS)
        if envelope.requires_admin is not True:
            raise BrokerValidationError(ElevationReason.NOT_ADMIN_OPERATION)
        if envelope.template_id != RESTART_SERVICE_TEMPLATE_ID or envelope.template_version != "1":
            raise BrokerValidationError(ElevationReason.TEMPLATE_MISMATCH)
        if parameter_digest(envelope.parameters) != envelope.parameter_digest:
            raise BrokerValidationError(ElevationReason.PARAMETER_DIGEST_MISMATCH)
        service_name = validate_restart_service_parameters(dict(envelope.parameters))
        expected_script = script_sha256(RESTART_SERVICE_SCRIPT)
        if envelope.script_sha256 != expected_script:
            raise BrokerValidationError(ElevationReason.SCRIPT_HASH_MISMATCH)
        # The catalog is reconstructed locally.  The parameter is not used for
        # interpolation; it is consumed by the fixed JSON-environment script.
        if hashlib.sha256(canonical_script(RESTART_SERVICE_SCRIPT)).hexdigest() != envelope.script_sha256:
            raise BrokerValidationError(ElevationReason.SCRIPT_HASH_MISMATCH)
        return service_name
