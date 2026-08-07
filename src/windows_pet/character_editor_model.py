"""Mutable, editor-only projection of immutable character package data."""
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QPixmap

from .character_models import CharacterPackage, PlaybackMode


@dataclass
class EditableFrame:
    frame_id: str
    relative_file: str
    duration_ms: int
    preview_pixmap: QPixmap
    source_path: Path


@dataclass
class EditableAnimation:
    event_id: str
    required: bool
    playback: PlaybackMode
    frames: list[EditableFrame]


@dataclass
class EditableCharacter:
    package_id: str
    name: str
    version: str
    author: str
    license_name: str
    animations: list[EditableAnimation]

    @classmethod
    def from_package(cls, package: CharacterPackage) -> "EditableCharacter":
        return cls(package.package_id, package.name, package.version, package.author, package.license_name,
                   [EditableAnimation(animation.event_id, animation.required, animation.playback,
                     [EditableFrame(frame.frame_id, frame.relative_file, frame.duration_ms, frame.pixmap,
                                    package.package_root / Path(*frame.relative_file.split("/")))
                      for frame in animation.frames]) for animation in package.animations.values()])

    def frame_id_for(self, event_id: str) -> str:
        existing = {frame.frame_id for animation in self.animations if animation.event_id == event_id for frame in animation.frames}
        while True:
            value = f"{event_id}_{uuid4().hex[:12]}"
            if value not in existing:
                return value
