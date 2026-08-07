from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFrame, QMessageBox, QPushButton, QSizePolicy

from windows_pet.character_editor_model import EditableCharacter
from windows_pet.character_editor_window import (FRAME_MIME, CharacterEditorWindow,
                                                  calculate_insert_index, move_frame,
                                                  natural_filename_key, validate_batch_capacity)
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


def _broken_editor(tmp_path):
    working = tmp_path / "characters" / "working"; working.mkdir(parents=True)
    (working / "manifest.json").write_bytes(b"broken working package")
    return working, CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), working)


def test_broken_working_is_preserved_and_builtin_fallback_is_rendered(tmp_path, qapp):
    working, editor = _broken_editor(tmp_path); before = (working / "manifest.json").read_bytes()
    assert (working / "manifest.json").read_bytes() == before
    assert editor.model.package_id == "default_pet"
    assert editor.status.text() == "編集データを読み込めません。既定キャラクターを表示しています。"
    assert editor.rebuild_button.isVisible() is False
    editor.show()
    assert editor.rebuild_button.isVisible()
    assert not editor.save_button.isEnabled()
    editor.close()


def test_broken_working_is_not_automatically_recreated(tmp_path, qapp, monkeypatch):
    working = tmp_path / "characters" / "working"; working.mkdir(parents=True)
    (working / "manifest.json").write_bytes(b"broken working package")
    monkeypatch.setattr("windows_pet.character_editor_window.create_working_from_builtin", lambda *args: (_ for _ in ()).throw(AssertionError("must not recreate automatically")))
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), working)
    assert editor.recovery_mode
    editor.close()


def test_rebuild_cancel_keeps_broken_working_byte_for_byte(tmp_path, qapp, monkeypatch):
    working, editor = _broken_editor(tmp_path); before = (working / "manifest.json").read_bytes()
    monkeypatch.setattr("windows_pet.character_editor_window.QMessageBox.question", lambda *args: QMessageBox.Cancel)
    editor.rebuild_from_builtin()
    assert (working / "manifest.json").read_bytes() == before
    editor.close()


def test_rebuild_approval_creates_valid_schema_v1_working(tmp_path, qapp, monkeypatch):
    working, editor = _broken_editor(tmp_path)
    monkeypatch.setattr("windows_pet.character_editor_window.QMessageBox.question", lambda *args: QMessageBox.Yes)
    editor.rebuild_from_builtin()
    package = load_character_package(working)
    assert package.schema_version == 1 and package.package_id == "default_pet_working"
    assert editor.model.package_id == "default_pet_working"
    assert not editor.rebuild_button.isVisible()
    editor.close()


def test_rebuild_failure_keeps_broken_working_and_fallback(tmp_path, qapp, monkeypatch):
    working, editor = _broken_editor(tmp_path); before = (working / "manifest.json").read_bytes()
    monkeypatch.setattr("windows_pet.character_editor_window.QMessageBox.question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr("windows_pet.character_editor_window.create_working_from_builtin", lambda *args: (_ for _ in ()).throw(OSError()))
    editor.rebuild_from_builtin()
    assert (working / "manifest.json").read_bytes() == before
    assert editor.model.package_id == "default_pet" and editor.recovery_mode
    editor.close()


def test_closing_editor_does_not_quit_application(tmp_path, qapp):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "working")
    calls = []
    qapp.aboutToQuit.connect(lambda: calls.append(True))
    editor.close()
    QApplication.processEvents()
    assert not calls


def test_editor_frame_rows_keep_complete_cards_in_horizontal_only_scroll_areas(tmp_path, qapp):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "working")
    editor.resize(1000, 700); editor.show(); QApplication.processEvents()
    assert len(editor._rows) == 4
    assert editor.preview.maximumHeight() <= 160
    for row, horizontal in zip(editor._rows, editor._frame_scroll_areas):
        cards = [child for child in horizontal.widget().findChildren(QFrame) if child.frameShape() == QFrame.StyledPanel]
        assert cards
        assert horizontal.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        assert horizontal.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        assert horizontal.minimumHeight() >= max(card.sizeHint().height() for card in cards)
        assert row.sizePolicy().verticalPolicy() == QSizePolicy.Fixed
        assert row.minimumHeight() == row.maximumHeight()
        assert all(card.minimumHeight() >= card.sizeHint().height() for card in cards)
    assert editor.content.minimumHeight() >= sum(row.minimumHeight() for row in editor._rows)
    assert editor.scroll.verticalScrollBarPolicy() != Qt.ScrollBarAlwaysOff
    editor.close()


def test_editor_add_and_delete_controls_preserve_frame_limits(tmp_path, qapp):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "working")
    animation = editor.model.animations[0]
    animation.frames = (animation.frames * 3)[:10]; editor._render()
    add = next(button for button in editor._rows[0].findChildren(QPushButton) if button.accessibleName() == "画像を追加")
    assert not add.isEnabled()
    animation.frames = animation.frames[:2]; editor._render()
    delete = next(button for button in editor._rows[0].findChildren(QPushButton) if button.accessibleName() == "フレームを削除")
    assert not delete.isEnabled()
    editor.close()


def test_reorder_helpers_preserve_identity_and_ignore_same_slot(tmp_path, qapp):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "working")
    frames = editor.model.animations[0].frames
    original = list(frames)
    assert calculate_insert_index([10, 30, 50], 30) == 2
    assert move_frame(frames, 2, 0)
    assert frames == [original[2], original[0], original[1], original[3]]
    assert not move_frame(frames, 1, 2)
    assert not editor.dirty
    editor.close()


def test_external_batch_is_naturally_sorted_and_all_or_nothing(tmp_path, qapp, monkeypatch):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "working")
    sources = []
    for name in ("frame_10.png", "frame_2.png", "frame_1.png"):
        path = tmp_path / name; _png(path); sources.append(path)
    animation = editor.model.animations[0]
    before = list(animation.frames)
    assert editor.add_external_frames(animation, sources, 1)
    assert [frame.source_path.name for frame in animation.frames[1:4]] != [path.name for path in sources]
    assert len(animation.frames) == len(before) + 3 and editor.dirty
    editor._set_dirty(False)
    invalid = tmp_path / "not-image.png"; invalid.write_bytes(b"nope")
    before = list(animation.frames)
    monkeypatch.setattr("windows_pet.character_editor_window.QMessageBox.warning", lambda *args: None)
    assert not editor.add_external_frames(animation, [sources[0], invalid], 0)
    assert animation.frames == before and not editor.dirty
    editor.close()


def test_drag_payload_is_session_and_event_bound(tmp_path, qapp):
    editor = CharacterEditorWindow(load_builtin_default_character(Path("assets/animations")), tmp_path / "working")
    strip = editor._frame_scroll_areas[0]
    frame = editor.model.animations[0].frames[0]
    from PySide6.QtCore import QMimeData
    import json
    mime = QMimeData()
    mime.setData(FRAME_MIME, json.dumps({"session": editor.session_token, "eventId": "idle", "frameId": frame.frame_id}).encode())
    assert strip._valid_internal(mime) is frame
    mime.setData(FRAME_MIME, json.dumps({"session": editor.session_token, "eventId": "sleep", "frameId": frame.frame_id}).encode())
    assert strip._valid_internal(mime) is None
    assert validate_batch_capacity(8, 2) and not validate_batch_capacity(8, 3)
    assert sorted([Path("frame_10.png"), Path("frame_2.png"), Path("frame_1.png")], key=natural_filename_key) == [Path("frame_1.png"), Path("frame_2.png"), Path("frame_10.png")]
    editor.close()
