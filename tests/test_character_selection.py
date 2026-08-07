import json
import zipfile
from pathlib import Path

from windows_pet.character_package_loader import load_character_package
from windows_pet.character_selection import CharacterSelection, export_wpet, import_wpet, read_selection, resolve_selection, save_selection
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
