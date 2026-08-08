from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .models import ElevationEnvelope, ElevationRequest

MAX_ENVELOPE_BYTES = 64 * 1024
MAX_NESTING = 6
ENVELOPE_KEYS = frozenset({
    "schema_version", "request_id", "proposal_id", "proposal_fingerprint", "grant_id",
    "operation_id", "template_id", "template_version", "script_sha256", "effect_class",
    "requires_admin", "timeout_seconds", "created_at", "expires_at", "nonce",
    "parameter_digest", "verification_plan_id", "parameters",
})


class EnvelopeFileError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def parameter_digest(parameters: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(parameters)).hexdigest()


class ElevationEnvelopeFactory:
    @staticmethod
    def create(request: ElevationRequest) -> ElevationEnvelope:
        if not isinstance(request, ElevationRequest):
            raise TypeError("request_required")
        return ElevationEnvelope(
            schema_version=1,
            request_id=request.request_id,
            proposal_id=request.proposal_id,
            proposal_fingerprint=request.proposal_fingerprint,
            grant_id=request.grant_id,
            operation_id=request.operation_id,
            template_id=request.template_id,
            template_version=request.template_version,
            script_sha256=request.script_sha256,
            effect_class=request.effect_class,
            requires_admin=request.requires_admin,
            timeout_seconds=request.timeout_seconds,
            created_at=request.created_at,
            expires_at=request.expires_at,
            nonce=request.nonce or secrets.token_urlsafe(24),
            parameter_digest=parameter_digest(request.parameters),
            verification_plan_id=request.verification_plan_id,
            parameters=request.parameters,
        )


def envelope_to_dict(envelope: ElevationEnvelope) -> dict[str, Any]:
    if not isinstance(envelope, ElevationEnvelope):
        raise TypeError("envelope_required")
    return {key: _jsonable(getattr(envelope, key)) for key in sorted(ENVELOPE_KEYS)}


def serialize_envelope(envelope: ElevationEnvelope) -> bytes:
    data = canonical_json_bytes(envelope_to_dict(envelope))
    if len(data) > MAX_ENVELOPE_BYTES:
        raise EnvelopeFileError("envelope_too_large")
    return data


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnvelopeFileError("duplicate_field")
        result[key] = value
    return result


def _depth(value, level=0):
    if level > MAX_NESTING:
        raise EnvelopeFileError("nesting_too_deep")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def deserialize_envelope(data: bytes | str) -> ElevationEnvelope:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if not isinstance(raw, bytes) or len(raw) > MAX_ENVELOPE_BYTES:
        raise EnvelopeFileError("envelope_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EnvelopeFileError("invalid_json") from error
    _depth(payload)
    if not isinstance(payload, dict) or set(payload) != ENVELOPE_KEYS:
        raise EnvelopeFileError("unknown_or_missing_field")
    try:
        envelope = ElevationEnvelope(
            schema_version=payload["schema_version"],
            request_id=payload["request_id"],
            proposal_id=payload["proposal_id"],
            proposal_fingerprint=payload["proposal_fingerprint"],
            grant_id=payload["grant_id"],
            operation_id=payload["operation_id"],
            template_id=payload["template_id"],
            template_version=payload["template_version"],
            script_sha256=payload["script_sha256"],
            effect_class=payload["effect_class"],
            requires_admin=payload["requires_admin"],
            timeout_seconds=payload["timeout_seconds"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
            nonce=payload["nonce"],
            parameter_digest=payload["parameter_digest"],
            verification_plan_id=payload["verification_plan_id"],
            parameters=payload["parameters"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EnvelopeFileError("invalid_envelope") from error
    if parameter_digest(envelope.parameters) != envelope.parameter_digest:
        raise EnvelopeFileError("parameter_digest_mismatch")
    if serialize_envelope(envelope) != raw:
        raise EnvelopeFileError("non_canonical_json")
    return envelope


def _reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _private_directory(directory: Path | None) -> Path:
    if directory is None:
        local = os.environ.get("LOCALAPPDATA")
        directory = Path(local) / "WindowsPet" / "elevation" if local else Path(tempfile.gettempdir()) / "WindowsPet" / "elevation"
    directory = Path(directory).resolve()
    if directory.exists() and _reparse(directory):
        raise EnvelopeFileError("private_directory_reparse_point")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def default_elevation_directory() -> Path:
    return _private_directory(None)


class EnvelopeFile:
    def __init__(self, path: Path, sha256: str):
        self.path = Path(path)
        self.sha256 = sha256

    def cleanup(self) -> bool:
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError:
            return False


def write_envelope_file(envelope: ElevationEnvelope, directory: Path | None = None) -> EnvelopeFile:
    data = serialize_envelope(envelope)
    root = _private_directory(directory)
    for _ in range(5):
        path = root / f"envelope-{secrets.token_hex(20)}.json"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            digest = hashlib.sha256(data).hexdigest()
            return EnvelopeFile(path, digest)
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    raise EnvelopeFileError("envelope_filename_collision")


def read_envelope_file(path: Path, *, expected_sha256: str | None = None,
                       root: Path | None = None) -> tuple[ElevationEnvelope, str]:
    path = Path(path)
    if not path.is_absolute() or path.name != path.name.strip() or path.is_dir() or _reparse(path):
        raise EnvelopeFileError("invalid_envelope_path")
    if root is not None and path.resolve().parent != Path(root).resolve():
        raise EnvelopeFileError("envelope_path_boundary")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise EnvelopeFileError("envelope_unreadable") from error
    if len(data) > MAX_ENVELOPE_BYTES:
        raise EnvelopeFileError("envelope_too_large")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise EnvelopeFileError("envelope_hash_mismatch")
    return deserialize_envelope(data), digest
