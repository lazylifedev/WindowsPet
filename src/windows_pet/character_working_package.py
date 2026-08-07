"""Safe construction and persistence of editable schema-v1 character packages."""
import json
import os
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QImageReader, QPixmap

from .character_editor_model import EditableCharacter
from .character_models import CharacterPackage, CharacterPackageError, CharacterPackageErrorCode
from .character_package_loader import (MAX_IMAGE_BYTES, MAX_IMAGE_HEIGHT, MAX_IMAGE_PIXELS,
                                       MAX_IMAGE_WIDTH, load_character_package)


class EditorImageError(ValueError): pass


def _regular_not_reparse(path: Path) -> None:
    try:
        info = path.lstat()
        if path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400) or not stat.S_ISREG(info.st_mode):
            raise EditorImageError()
    except OSError as exc:
        raise EditorImageError() from exc


def validate_png(path: Path) -> QPixmap:
    _regular_not_reparse(path)
    try:
        if path.suffix.lower() != ".png" or path.stat().st_size > MAX_IMAGE_BYTES:
            raise EditorImageError()
    except OSError as exc:
        raise EditorImageError() from exc
    reader = QImageReader(str(path), b"png"); image = reader.read()
    if image.isNull() or bytes(reader.format()).lower() != b"png": raise EditorImageError()
    if not (1 <= image.width() <= MAX_IMAGE_WIDTH and 1 <= image.height() <= MAX_IMAGE_HEIGHT and image.width() * image.height() <= MAX_IMAGE_PIXELS):
        raise EditorImageError()
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull(): raise EditorImageError()
    return pixmap


def _copy_png(source: Path, target: Path) -> None:
    validate_png(source); target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target); validate_png(target)


def _manifest(model: EditableCharacter) -> dict:
    return {"schemaVersion": 1, "id": model.package_id, "name": model.name, "version": model.version,
            "author": model.author, "license": model.license_name,
            "animations": {animation.event_id: {"required": animation.required,
              "playback": animation.playback.value,
              "frames": [{"id": frame.frame_id, "file": frame.relative_file, "durationMs": frame.duration_ms}
                         for frame in animation.frames]} for animation in model.animations}}


def _build(model: EditableCharacter, destination: Path) -> CharacterPackage:
    for animation in model.animations:
        for frame in animation.frames:
            _copy_png(frame.source_path, destination / Path(*frame.relative_file.split("/")))
    (destination / "manifest.json").write_text(json.dumps(_manifest(model), ensure_ascii=False, indent=2), encoding="utf-8")
    return load_character_package(destination)


def _replace_atomically(candidate: Path, working: Path) -> None:
    backup = working.parent / f".working.backup-{uuid4().hex}"
    moved = False
    try:
        if working.exists():
            os.replace(working, backup); moved = True
        os.replace(candidate, working)
    except OSError:
        if moved and not working.exists() and backup.exists(): os.replace(backup, working)
        raise
    finally:
        # Never delete the only known-good package if a Windows rename failed.
        if working.exists() and backup.exists() and backup.is_dir(): shutil.rmtree(backup)


def save_working_package(model: EditableCharacter, working: Path) -> CharacterPackage:
    working = Path(working); working.parent.mkdir(parents=True, exist_ok=True)
    temporary = working.parent / f".working.tmp-{uuid4().hex}"
    try:
        temporary.mkdir(); package = _build(model, temporary)
        _replace_atomically(temporary, working)
        return load_character_package(working)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


def create_working_from_builtin(package: CharacterPackage, working: Path) -> CharacterPackage:
    model = EditableCharacter.from_package(package)
    model.package_id, model.version = "default_pet_working", "1.0.0"
    for animation in model.animations:
        for frame in animation.frames:
            frame.relative_file = f"animations/{animation.event_id}/{Path(frame.relative_file).name}"
    return save_working_package(model, working)
