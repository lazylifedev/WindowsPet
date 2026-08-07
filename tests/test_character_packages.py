import json
import subprocess
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from windows_pet.character_models import CharacterPackageError, CharacterPackageErrorCode, PlaybackMode
from windows_pet.character_package_loader import load_builtin_default_character, load_character_package, load_character_with_fallback
from windows_pet.main import PetWindow


def _png(path: Path, width: int = 2, height: int = 2) -> None:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#44aaee"))
    assert image.save(str(path), "PNG")


def _package(root: Path, **changes):
    _png(root / "frame.png")
    animations = {}
    for event, playback in (("idle", "loop"), ("sleep", "loop"), ("thinking", "loop"), ("wave", "once")):
        animations[event] = {"required": True, "playback": playback, "frames": [
            {"id": f"{event}_1", "file": "frame.png", "durationMs": 50},
            {"id": f"{event}_2", "file": "frame.png", "durationMs": 5000},
        ]}
    value = {"schemaVersion": 1, "id": "sample_pet", "name": "Sample Pet", "version": "1.0", "animations": animations}
    value.update(changes)
    (root / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def _error(root: Path) -> CharacterPackageErrorCode:
    with pytest.raises(CharacterPackageError) as raised:
        load_character_package(root)
    return raised.value.code


def test_schema_loads_immutable_models_and_optional_event(tmp_path, qapp):
    data = _package(tmp_path)
    data["animations"]["celebrate"] = {"required": False, "playback": "once", "frames": [
        {"id": "celebrate_1", "file": "frame.png", "durationMs": 60},
        {"id": "celebrate_2", "file": "frame.png", "durationMs": 70},
    ]}
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8-sig")
    package = load_character_package(tmp_path)
    assert package.animations["celebrate"].playback is PlaybackMode.ONCE
    assert [frame.duration_ms for frame in package.animations["idle"].frames] == [50, 5000]
    with pytest.raises(TypeError):
        package.animations["other"] = package.animations["idle"]


@pytest.mark.parametrize("change", [
    {"required": True, "playback": "once"},
    {"required": False, "playback": "loop"},
])
def test_known_optional_events_must_be_optional_once(tmp_path, qapp, change):
    data = _package(tmp_path)
    data["animations"]["single_click"] = {**change, "frames": [
        {"id": "click_1", "file": "frame.png", "durationMs": 60},
        {"id": "click_2", "file": "frame.png", "durationMs": 60},
    ]}
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert _error(tmp_path) is CharacterPackageErrorCode.INVALID_PLAYBACK


@pytest.mark.parametrize(("mutation", "code"), [
    (lambda d: d.update(schemaVersion=2), CharacterPackageErrorCode.UNSUPPORTED_SCHEMA),
    (lambda d: d.update(unexpected=True), CharacterPackageErrorCode.INVALID_MANIFEST),
    (lambda d: d["animations"].pop("idle"), CharacterPackageErrorCode.MISSING_REQUIRED_EVENT),
    (lambda d: d["animations"]["idle"].update(required=False), CharacterPackageErrorCode.MISSING_REQUIRED_EVENT),
    (lambda d: d["animations"]["idle"].update(playback="once"), CharacterPackageErrorCode.INVALID_PLAYBACK),
    (lambda d: d["animations"]["idle"].update(frames=d["animations"]["idle"]["frames"][:1]), CharacterPackageErrorCode.INVALID_FRAME_COUNT),
    (lambda d: d["animations"]["idle"]["frames"].append({"id": "idle_1", "file": "frame.png", "durationMs": 50}), CharacterPackageErrorCode.DUPLICATE_FRAME_ID),
    (lambda d: d["animations"]["idle"]["frames"][0].update(durationMs=True), CharacterPackageErrorCode.INVALID_DURATION),
    (lambda d: d["animations"]["idle"]["frames"][0].update(durationMs=49), CharacterPackageErrorCode.INVALID_DURATION),
])
def test_schema_rejects_invalid_events_and_frames(tmp_path, qapp, mutation, code):
    data = _package(tmp_path); mutation(data)
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert _error(tmp_path) is code


@pytest.mark.parametrize("content, code", [
    (b'{"schemaVersion":NaN}', CharacterPackageErrorCode.INVALID_JSON),
    (b'{"id":"one","id":"two"}', CharacterPackageErrorCode.DUPLICATE_JSON_KEY),
    (b'{} trailing', CharacterPackageErrorCode.INVALID_JSON),
    ('{}'.encode('utf-16'), CharacterPackageErrorCode.INVALID_UTF8),
    (b'\xff', CharacterPackageErrorCode.INVALID_UTF8),
])
def test_manifest_encoding_and_json_are_strict(tmp_path, qapp, content, code):
    (tmp_path / "manifest.json").write_bytes(content)
    assert _error(tmp_path) is code


@pytest.mark.parametrize("path", ["../frame.png", "/frame.png", "C:/frame.png", "//server/share.png", "file:///x.png", "https://x.png", "%USERPROFILE%/x.png", "a//b.png"])
def test_frame_paths_are_relative_and_safe(tmp_path, qapp, path):
    data = _package(tmp_path)
    data["animations"]["idle"]["frames"][0]["file"] = path
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert _error(tmp_path) is CharacterPackageErrorCode.PATH_ESCAPE


@pytest.mark.parametrize("filename", ["run.py", "run.exe", "shortcut.lnk", "unknown.bin"])
def test_package_rejects_executable_and_unknown_files(tmp_path, qapp, filename):
    _package(tmp_path); (tmp_path / filename).write_bytes(b"x")
    assert _error(tmp_path) is CharacterPackageErrorCode.PROHIBITED_FILE


def test_invalid_png_is_rejected_without_executing_any_process(tmp_path, qapp, monkeypatch):
    _package(tmp_path)
    (tmp_path / "frame.png").write_bytes(b"not a png")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("process execution is forbidden"))
    assert _error(tmp_path) is CharacterPackageErrorCode.INVALID_IMAGE


def test_reparse_point_is_rejected_when_supported(tmp_path, qapp):
    _package(tmp_path)
    linked = tmp_path / "linked.png"
    try:
        linked.symlink_to(tmp_path / "frame.png")
    except OSError:
        pytest.skip("symlink creation is not available on this Windows test host")
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    data["animations"]["idle"]["frames"][0]["file"] = "linked.png"
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert _error(tmp_path) is CharacterPackageErrorCode.REPARSE_POINT


def test_fallback_and_trusted_legacy_adapter(tmp_path, qapp):
    builtin = Path("assets/animations")
    legacy = load_builtin_default_character(builtin)
    assert set(legacy.animations) == {"idle", "sleep", "thinking", "wave"}
    assert legacy.animations["wave"].playback is PlaybackMode.ONCE
    assert legacy.animations["idle"].frames[0].duration_ms == 250
    _package(tmp_path)
    assert not load_character_with_fallback(tmp_path, builtin).fallback_used
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    result = load_character_with_fallback(tmp_path, builtin)
    assert result.fallback_used and result.error_code is CharacterPackageErrorCode.INVALID_MANIFEST


def test_player_uses_each_frame_duration_and_once_returns_to_idle(tmp_path, qapp):
    package = load_builtin_default_character(Path("assets/animations"))
    pet = PetWindow(package.animations, tmp_path / "position.json", quit_callback=lambda: None)
    pet.play("wave")
    assert pet._timer.interval() == package.animations["wave"].frames[0].duration_ms
    pet._frame = len(pet._animation.frames) - 1
    pet._next_frame()
    assert pet._animation.event_id == "idle"
    pet.close()
