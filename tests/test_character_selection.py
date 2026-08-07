import json
import zipfile
from pathlib import Path

from windows_pet.character_package_loader import load_character_package
from windows_pet.character_selection import CharacterSelection, export_wpet, import_wpet, read_selection, resolve_selection, save_selection
from windows_pet.main import PetWindow
from windows_pet.character_package_loader import load_builtin_default_character
from test_character_packages import _package


def test_selection_roundtrip_and_invalid_selection_falls_back(tmp_path, qapp):
    selection_file = tmp_path / "selection.json"
    save_selection(selection_file, CharacterSelection("working", "sample_pet", "1.0"))
    assert read_selection(selection_file) == CharacterSelection("working", "sample_pet", "1.0")
    selection_file.write_text('{"schemaVersion":1,"source":"working","packageId":"x","version":"1","path":"C:/bad"}', encoding="utf-8")
    assert read_selection(selection_file) is None
    package, selection, fallback = resolve_selection(selection_file, tmp_path / "missing", tmp_path / "installed", Path("assets/animations"))
    assert not fallback and selection.source == "builtin" and package.package_id == "default_pet"


def test_wpet_export_reopens_and_import_installs(tmp_path, qapp):
    source = tmp_path / "source"; source.mkdir(); _package(source)
    package = load_character_package(source); archive = tmp_path / "pet.wpet"
    export_wpet(package, archive)
    with zipfile.ZipFile(archive) as content:
        assert "manifest.json" in content.namelist()
    installed = import_wpet(archive, tmp_path / "characters" / "installed")
    assert installed.package_id == "sample_pet"


def test_wpet_rejects_traversal_without_install(tmp_path, qapp):
    archive = tmp_path / "bad.wpet"
    with zipfile.ZipFile(archive, "w") as content: content.writestr("../manifest.json", "{}")
    try: import_wpet(archive, tmp_path / "installed")
    except ValueError: pass
    else: assert False, "unsafe archive was accepted"
    assert not (tmp_path / "installed").exists()


def test_builtin_selection_identity_is_validated_and_repaired(tmp_path, qapp):
    path = tmp_path / "selection.json"
    save_selection(path, CharacterSelection("builtin", "wrong", "bundled"))
    package, selection, fallback = resolve_selection(path, tmp_path / "working", tmp_path / "installed", Path("assets/animations"))
    assert fallback and package.package_id == selection.package_id == "default_pet"
    assert read_selection(path) == selection


def test_selection_write_failure_rolls_back_runtime_and_persistence(tmp_path, qapp, monkeypatch):
    builtin = load_builtin_default_character(Path("assets/animations"))
    selection_path = tmp_path / "selection.json"
    old = CharacterSelection("builtin", builtin.package_id, builtin.version)
    save_selection(selection_path, old)
    pet = PetWindow(builtin.animations, tmp_path / "position.json", quit_callback=lambda: None, character_package=builtin, selection_path=selection_path, character_selection=old)
    candidate_root = tmp_path / "candidate"; candidate_root.mkdir(); _package(candidate_root)
    candidate = load_character_package(candidate_root)
    monkeypatch.setattr("windows_pet.main.save_selection", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    assert not pet.select_character(candidate, CharacterSelection("working", candidate.package_id, candidate.version))
    assert pet.current_character_package == builtin
    assert pet.current_character_selection == old
    assert read_selection(selection_path) == old
    pet.close()


def test_same_id_import_requires_explicit_replace(tmp_path, qapp):
    source = tmp_path / "source"; source.mkdir(); _package(source)
    archive = tmp_path / "pet.wpet"; export_wpet(load_character_package(source), archive)
    installed_root = tmp_path / "installed"
    assert import_wpet(archive, installed_root).package_id == "sample_pet"
    try: import_wpet(archive, installed_root)
    except FileExistsError: pass
    else: assert False, "same-ID archive replaced an installed package without confirmation"
    assert import_wpet(archive, installed_root, replace_existing=True).package_id == "sample_pet"
