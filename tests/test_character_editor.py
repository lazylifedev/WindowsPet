from pathlib import Path

from PySide6.QtGui import QColor, QImage

from windows_pet.character_editor_model import EditableCharacter
from windows_pet.character_editor_window import CharacterEditorWindow
from windows_pet.character_package_loader import load_builtin_default_character, load_character_package
from windows_pet.character_working_package import create_working_from_builtin, save_working_package, validate_png


def _png(path: Path):
    image = QImage(3, 3, QImage.Format_ARGB32); image.fill(QColor("#44aaee")); assert image.save(str(path), "PNG")


def test_builtin_is_copied_to_schema_v1_working_package(tmp_path, qapp):
    builtin_root = Path("assets/animations")
    before = {path.relative_to(builtin_root): path.read_bytes() for path in builtin_root.rglob("*") if path.is_file()}
    package = create_working_from_builtin(load_builtin_default_character(builtin_root), tmp_path / "characters" / "working")
    assert package.package_id == "default_pet_working"
    assert package.animations["wave"].playback.value == "once"
    assert [f.duration_ms for f in package.animations["idle"].frames] == [250] * 4
    assert load_character_package(tmp_path / "characters" / "working").package_id == "default_pet_working"
    assert before == {path.relative_to(builtin_root): path.read_bytes() for path in builtin_root.rglob("*") if path.is_file()}


def test_save_keeps_existing_working_when_candidate_is_invalid(tmp_path, qapp):
    working = tmp_path / "working"; package = create_working_from_builtin(load_builtin_default_character(Path("assets/animations")), working)
    before = (working / "manifest.json").read_bytes(); model = EditableCharacter.from_package(package)
    model.animations[0].frames[0].duration_ms = 49
    try: save_working_package(model, working)
    except Exception: pass
    assert (working / "manifest.json").read_bytes() == before


def test_validate_png_rejects_fake_png(tmp_path, qapp):
    fake = tmp_path / "fake.png"; fake.write_bytes(b"not png")
    try: validate_png(fake)
    except ValueError: return
    assert False, "fake PNG must be rejected"


def test_editor_model_add_frame_uses_non_positional_unique_id(tmp_path, qapp):
    model = EditableCharacter.from_package(load_builtin_default_character(Path("assets/animations")))
    first, second = model.frame_id_for("idle"), model.frame_id_for("idle")
    assert first != second and first.startswith("idle_") and second.startswith("idle_")


def test_editor_renders_required_events_and_duration_controls(tmp_path, qapp):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "characters" / "working")
    labels = [label.text() for label in editor.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any("idle" in label and "必須" in label and "4 / 10" in label for label in labels)
    spins = editor.findChildren(__import__("PySide6.QtWidgets", fromlist=["QSpinBox"]).QSpinBox)
    assert spins and all(spin.minimum() == 50 and spin.maximum() == 5000 and spin.singleStep() == 50 for spin in spins)
    assert not editor.save_button.isEnabled()
    spins[0].setValue(300)
    assert editor.save_button.isEnabled()
    editor._set_dirty(False)
    editor.close()
