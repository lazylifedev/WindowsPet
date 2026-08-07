"""Selection persistence and safe .wpet distribution handling."""
import json
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .character_models import CharacterPackage, CharacterPackageError
from .character_package_loader import MAX_PACKAGE_ENTRIES, load_builtin_default_character, load_character_package

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


@dataclass(frozen=True)
class CharacterSelection:
    source: str
    package_id: str
    version: str


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def save_selection(path: Path, selection: CharacterSelection) -> None:
    if selection.source not in {"builtin", "working", "installed"}:
        raise ValueError("invalid source")
    _atomic_json(Path(path), {"schemaVersion": 1, "source": selection.source, "packageId": selection.package_id, "version": selection.version})


def read_selection(path: Path) -> CharacterSelection | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(value) != {"schemaVersion", "source", "packageId", "version"} or value["schemaVersion"] != 1:
            return None
        if value["source"] not in {"builtin", "working", "installed"} or not all(isinstance(value[k], str) and value[k] for k in ("packageId", "version")):
            return None
        return CharacterSelection(value["source"], value["packageId"], value["version"])
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def resolve_selection(selection_path: Path, working_root: Path, installed_root: Path, builtin_root: Path) -> tuple[CharacterPackage, CharacterSelection, bool]:
    selected = read_selection(selection_path)
    builtin = lambda: load_builtin_default_character(builtin_root)
    try:
        if selected is None or selected.source == "builtin":
            package = builtin(); return package, CharacterSelection("builtin", package.package_id, package.version), selected is not None
        root = Path(working_root) if selected.source == "working" else Path(installed_root) / selected.package_id
        if selected.source == "installed" and (root.name != selected.package_id or root.is_symlink()): raise ValueError("invalid installed root")
        package = load_character_package(root)
        if package.package_id != selected.package_id or package.version != selected.version: raise CharacterPackageError
        return package, selected, False
    except (CharacterPackageError, OSError, ValueError):
        package = builtin(); fallback = CharacterSelection("builtin", package.package_id, package.version)
        try: save_selection(selection_path, fallback)
        except OSError: pass
        return package, fallback, True


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    path = PurePosixPath(name)
    if not name or "\\" in name or name.startswith(("/", "\\")) or ":" in name or path.name == "" or any(p in {"", ".", ".."} for p in path.parts):
        raise ValueError("unsafe archive path")
    if info.is_dir() or ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
        raise ValueError("unsafe archive entry")
    if info.flag_bits & 1 or path.suffix.lower() in {".exe", ".dll", ".ps1", ".bat", ".cmd", ".js", ".py", ".zip", ".wpet"}:
        raise ValueError("prohibited archive entry")
    return path


def import_wpet(archive: Path, installed_root: Path) -> CharacterPackage:
    archive = Path(archive)
    if archive.suffix.lower() != ".wpet": raise ValueError("expected .wpet")
    temporary = Path(installed_root).parent / f".import-{uuid4().hex}"
    try:
        with zipfile.ZipFile(archive) as package_zip:
            infos = package_zip.infolist(); names: set[str] = set(); total = 0
            if not 1 <= len(infos) <= MAX_PACKAGE_ENTRIES: raise ValueError("archive entry limit")
            for info in infos:
                path = _safe_member(info); key = str(path).casefold()
                if key in names: raise ValueError("duplicate archive entry")
                names.add(key); total += info.file_size
                if total > MAX_ARCHIVE_BYTES or (info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO): raise ValueError("archive size limit")
            if "manifest.json" not in names: raise ValueError("missing root manifest")
            temporary.mkdir(parents=True)
            for info in infos:
                target = temporary.joinpath(*_safe_member(info).parts); target.parent.mkdir(parents=True, exist_ok=True)
                with package_zip.open(info) as source, target.open("wb") as output: shutil.copyfileobj(source, output)
        candidate = load_character_package(temporary)
        destination = Path(installed_root) / candidate.package_id; destination.parent.mkdir(parents=True, exist_ok=True)
        backup = destination.with_name(f".backup-{uuid4().hex}")
        if destination.exists(): os.replace(destination, backup)
        try: os.replace(temporary, destination)
        except OSError:
            if backup.exists(): os.replace(backup, destination)
            raise
        if backup.exists(): shutil.rmtree(backup)
        return load_character_package(destination)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


def export_wpet(package: CharacterPackage, destination: Path) -> None:
    if package.package_id == "default_pet": raise ValueError("builtin packages cannot be exported")
    package = load_character_package(package.package_root)
    destination = Path(destination)
    if destination.suffix.lower() != ".wpet": destination = destination.with_suffix(".wpet")
    temporary = destination.with_name(destination.name + f".tmp-{uuid4().hex}")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as output:
            output.write(package.package_root / "manifest.json", "manifest.json")
            names = {"manifest.json"}
            for animation in package.animations.values():
                for frame in animation.frames: names.add(frame.relative_file)
            if package.thumbnail: names.add(package.thumbnail)
            for relative in sorted(names - {"manifest.json"}): output.write(package.package_root / Path(*relative.split("/")), relative)
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): temporary.unlink()
