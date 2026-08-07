"""Strict loader for untrusted, display-only character packages."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImageReader, QPixmap

from .character_models import CharacterAnimation, CharacterFrame, CharacterLoadResult, CharacterPackage, CharacterPackageError, CharacterPackageErrorCode, PlaybackMode

MAX_ANIMATIONS = 64
MAX_TOTAL_FRAMES = 640
MAX_MANIFEST_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_WIDTH = MAX_IMAGE_HEIGHT = 4096
MAX_IMAGE_PIXELS = 16_777_216
MAX_PACKAGE_TOTAL_PIXELS = 67_108_864
MAX_PACKAGE_ENTRIES = 1000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED_EVENTS = {"idle": PlaybackMode.LOOP, "sleep": PlaybackMode.LOOP, "thinking": PlaybackMode.LOOP, "wave": None}
OPTIONAL_EVENTS = {"single_click", "double_click", "right_click", "hover_long", "drag_start", "drag_end"}
FORBIDDEN_SUFFIXES = {".py", ".pyw", ".ps1", ".bat", ".cmd", ".exe", ".dll", ".com", ".scr", ".msi", ".js", ".vbs", ".vbe", ".jse", ".ws", ".wsf", ".wsh", ".hta", ".jar", ".lnk", ".url"}
ALLOWED_SUFFIXES = {".png", ".txt", ".md"}


def _fail(code: CharacterPackageErrorCode) -> None:
    raise CharacterPackageError(code)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        _fail(CharacterPackageErrorCode.IMAGE_NOT_FOUND)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _normal(path: Path, missing: CharacterPackageErrorCode = CharacterPackageErrorCode.IMAGE_NOT_FOUND) -> None:
    if not path.exists():
        _fail(missing)
    current = path
    while True:
        if _is_reparse(current):
            _fail(CharacterPackageErrorCode.REPARSE_POINT)
        if current.parent == current:
            return
        current = current.parent


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(CharacterPackageErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def _constant(_: str) -> None:
    _fail(CharacterPackageErrorCode.INVALID_JSON)


def _manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        _fail(CharacterPackageErrorCode.MANIFEST_NOT_FOUND)
    _normal(path, CharacterPackageErrorCode.MANIFEST_NOT_FOUND)
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            _fail(CharacterPackageErrorCode.MANIFEST_TOO_LARGE)
        raw = path.read_bytes()
    except CharacterPackageError:
        raise
    except OSError:
        _fail(CharacterPackageErrorCode.MANIFEST_NOT_FOUND)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        _fail(CharacterPackageErrorCode.INVALID_UTF8)
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except CharacterPackageError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        _fail(CharacterPackageErrorCode.INVALID_JSON)
    if not isinstance(value, dict):
        _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
    return value


def _keys(value: Any, allowed: set[str], required: set[str] = set()) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
    return value


def _identifier(value: Any, code: CharacterPackageErrorCode = CharacterPackageErrorCode.INVALID_MANIFEST) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _fail(code)
    return value


def _text(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _relative(value: Any, root: Path) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value or ":" in value or "://" in value:
        _fail(CharacterPackageErrorCode.PATH_ESCAPE)
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(CharacterPackageErrorCode.PATH_ESCAPE)
    path = root.joinpath(*parts)
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        _fail(CharacterPackageErrorCode.PATH_ESCAPE)
    return value, path


def _scan(root: Path) -> None:
    count = 0
    for current, directories, files in os.walk(root, followlinks=False):
        directory_path = Path(current)
        if _is_reparse(directory_path):
            _fail(CharacterPackageErrorCode.REPARSE_POINT)
        for name in [*directories, *files]:
            count += 1
            if count > MAX_PACKAGE_ENTRIES:
                _fail(CharacterPackageErrorCode.RESOURCE_LIMIT)
            path = directory_path / name
            if _is_reparse(path):
                _fail(CharacterPackageErrorCode.REPARSE_POINT)
            if path.is_file():
                suffix = path.suffix.lower()
                if suffix in FORBIDDEN_SUFFIXES or (suffix not in ALLOWED_SUFFIXES and name not in {"manifest.json", "LICENSE", "LICENSE.txt"}):
                    _fail(CharacterPackageErrorCode.PROHIBITED_FILE)


def _png(root: Path, relative_file: str) -> tuple[QPixmap, int]:
    _, path = _relative(relative_file, root)
    _normal(path)
    try:
        if not stat.S_ISREG(path.stat().st_mode) or path.suffix.lower() != ".png" or path.stat().st_size > MAX_IMAGE_BYTES:
            _fail(CharacterPackageErrorCode.INVALID_IMAGE)
    except OSError:
        _fail(CharacterPackageErrorCode.IMAGE_NOT_FOUND)
    reader = QImageReader(str(path), b"png")
    image = reader.read()
    if image.isNull() or bytes(reader.format()).lower() != b"png":
        _fail(CharacterPackageErrorCode.INVALID_IMAGE)
    width, height = image.width(), image.height()
    pixels = width * height
    if width < 1 or height < 1 or width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT or pixels > MAX_IMAGE_PIXELS:
        _fail(CharacterPackageErrorCode.RESOURCE_LIMIT)
    _normal(path)  # Revalidate immediately before UI-thread pixmap creation.
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        _fail(CharacterPackageErrorCode.INVALID_IMAGE)
    return pixmap, pixels


def load_character_package(package_root: Path) -> CharacterPackage:
    root = Path(package_root)
    _normal(root, CharacterPackageErrorCode.MANIFEST_NOT_FOUND)
    if not root.is_dir():
        _fail(CharacterPackageErrorCode.MANIFEST_NOT_FOUND)
    _scan(root)
    data = _manifest(root / "manifest.json")
    top = _keys(data, {"schemaVersion", "id", "name", "version", "author", "license", "thumbnail", "animations"}, {"schemaVersion", "id", "name", "version", "animations"})
    if type(top["schemaVersion"]) is not int or top["schemaVersion"] != 1:
        _fail(CharacterPackageErrorCode.UNSUPPORTED_SCHEMA)
    package_id = _identifier(top["id"], CharacterPackageErrorCode.INVALID_PACKAGE_ID)
    if not _text(top["name"], 1, 100) or not _text(top["version"], 1, 50):
        _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
    author, license_name = top.get("author", ""), top.get("license", "")
    if not _text(author, 0, 100) or not _text(license_name, 0, 100):
        _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
    thumbnail = top.get("thumbnail")
    if thumbnail is not None:
        if not isinstance(thumbnail, str):
            _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
        _png(root, thumbnail)
    raw_animations = top["animations"]
    if not isinstance(raw_animations, dict) or not raw_animations or len(raw_animations) > MAX_ANIMATIONS:
        _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
    animations: dict[str, CharacterAnimation] = {}
    total_frames = total_pixels = 0
    for event_id, raw_animation in raw_animations.items():
        _identifier(event_id)
        animation = _keys(raw_animation, {"required", "playback", "frames"}, {"required", "playback", "frames"})
        if type(animation["required"]) is not bool:
            _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
        try:
            playback = PlaybackMode(animation["playback"])
        except (TypeError, ValueError):
            _fail(CharacterPackageErrorCode.INVALID_PLAYBACK)
        raw_frames = animation["frames"]
        if not isinstance(raw_frames, list) or not 2 <= len(raw_frames) <= 10:
            _fail(CharacterPackageErrorCode.INVALID_FRAME_COUNT)
        frames: list[CharacterFrame] = []
        ids: set[str] = set()
        for raw_frame in raw_frames:
            frame = _keys(raw_frame, {"id", "file", "durationMs"}, {"id", "file", "durationMs"})
            frame_id = _identifier(frame["id"])
            if frame_id in ids:
                _fail(CharacterPackageErrorCode.DUPLICATE_FRAME_ID)
            ids.add(frame_id)
            duration = frame["durationMs"]
            if type(duration) is not int or not 50 <= duration <= 5000:
                _fail(CharacterPackageErrorCode.INVALID_DURATION)
            relative_file, _ = _relative(frame["file"], root)
            pixmap, pixels = _png(root, relative_file)
            total_frames, total_pixels = total_frames + 1, total_pixels + pixels
            if total_frames > MAX_TOTAL_FRAMES or total_pixels > MAX_PACKAGE_TOTAL_PIXELS:
                _fail(CharacterPackageErrorCode.RESOURCE_LIMIT)
            frames.append(CharacterFrame(frame_id, relative_file, duration, pixmap))
        animations[event_id] = CharacterAnimation(event_id, animation["required"], playback, tuple(frames))
    for event_id, expected in REQUIRED_EVENTS.items():
        animation = animations.get(event_id)
        if animation is None or not animation.required:
            _fail(CharacterPackageErrorCode.MISSING_REQUIRED_EVENT)
        if expected is not None and animation.playback is not expected:
            _fail(CharacterPackageErrorCode.INVALID_PLAYBACK)
    for event_id, animation in animations.items():
        if event_id in OPTIONAL_EVENTS and (animation.required or animation.playback is not PlaybackMode.ONCE):
            _fail(CharacterPackageErrorCode.INVALID_PLAYBACK)
    return CharacterPackage(1, package_id, top["name"], top["version"], author, license_name, thumbnail, root, animations)


def load_builtin_default_character(legacy_root: Path) -> CharacterPackage:
    root = Path(legacy_root)
    raw_animations = _manifest(root / "manifest.json").get("animations")
    if not isinstance(raw_animations, dict):
        _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
    animations: dict[str, CharacterAnimation] = {}
    for event_id in ("idle", "sleep", "thinking", "wave"):
        raw = raw_animations.get(event_id)
        if not isinstance(raw, dict) or type(raw.get("fps_recommended")) is not int or not isinstance(raw.get("frames"), list):
            _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
        duration = round(1000 / raw["fps_recommended"])
        if not 50 <= duration <= 5000 or len(raw["frames"]) < 2:
            _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
        frames = []
        for index, item in enumerate(raw["frames"]):
            if not isinstance(item, dict) or not isinstance(item.get("file"), str):
                _fail(CharacterPackageErrorCode.INVALID_MANIFEST)
            relative = f"{event_id}/{item['file']}"
            pixmap, _ = _png(root, relative)
            frames.append(CharacterFrame(f"{event_id}_{index + 1:02d}", relative, duration, pixmap))
        animations[event_id] = CharacterAnimation(event_id, True, PlaybackMode.ONCE if event_id == "wave" else PlaybackMode.LOOP, tuple(frames))
    return CharacterPackage(1, "default_pet", "WindowsPet", "bundled", "Lazy Life Dev.", "Proprietary", None, root, animations)


def load_character_with_fallback(selected_package_root: Path | None, builtin_legacy_root: Path) -> CharacterLoadResult:
    if selected_package_root is None:
        return CharacterLoadResult(load_builtin_default_character(builtin_legacy_root), False, None)
    try:
        return CharacterLoadResult(load_character_package(selected_package_root), False, None)
    except CharacterPackageError as exc:
        return CharacterLoadResult(load_builtin_default_character(builtin_legacy_root), True, exc.code)
