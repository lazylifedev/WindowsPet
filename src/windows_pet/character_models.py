"""Immutable, display-only character package data."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from PySide6.QtGui import QPixmap


class PlaybackMode(str, Enum):
    LOOP = "loop"
    ONCE = "once"


@dataclass(frozen=True)
class CharacterFrame:
    frame_id: str
    relative_file: str
    duration_ms: int
    pixmap: QPixmap


@dataclass(frozen=True)
class CharacterAnimation:
    event_id: str
    required: bool
    playback: PlaybackMode
    frames: tuple[CharacterFrame, ...]


@dataclass(frozen=True)
class CharacterPackage:
    schema_version: int
    package_id: str
    name: str
    version: str
    author: str
    license_name: str
    thumbnail: str | None
    package_root: Path
    animations: Mapping[str, CharacterAnimation]
    thumbnail_pixmap: QPixmap | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "animations", MappingProxyType(dict(self.animations)))


class CharacterPackageErrorCode(str, Enum):
    MANIFEST_NOT_FOUND = "manifest_not_found"
    MANIFEST_TOO_LARGE = "manifest_too_large"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    INVALID_MANIFEST = "invalid_manifest"
    INVALID_PACKAGE_ID = "invalid_package_id"
    MISSING_REQUIRED_EVENT = "missing_required_event"
    INVALID_PLAYBACK = "invalid_playback"
    INVALID_FRAME_COUNT = "invalid_frame_count"
    INVALID_DURATION = "invalid_duration"
    DUPLICATE_FRAME_ID = "duplicate_frame_id"
    PATH_ESCAPE = "path_escape"
    REPARSE_POINT = "reparse_point"
    PROHIBITED_FILE = "prohibited_file"
    IMAGE_NOT_FOUND = "image_not_found"
    INVALID_IMAGE = "invalid_image"
    RESOURCE_LIMIT = "resource_limit"


class CharacterPackageError(RuntimeError):
    def __init__(self, code: CharacterPackageErrorCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class CharacterLoadResult:
    package: CharacterPackage
    fallback_used: bool
    error_code: CharacterPackageErrorCode | None
