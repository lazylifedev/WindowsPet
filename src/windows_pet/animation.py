import json
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtGui import QPixmap

@dataclass(frozen=True)
class Animation:
    name: str
    frames: tuple[QPixmap, ...]
    fps: int
    loop: bool

def load_animations(root: Path) -> dict[str, Animation]:
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"manifest.json を読み込めません: {exc}") from exc
    result = {}
    for name, spec in manifest.get("animations", {}).items():
        count = int(spec["frame_count"])
        files = spec.get("frames", [])
        if len(files) < count:
            raise RuntimeError(f"{name} のフレーム定義が不足しています")
        frames = []
        for item in files[:count]:
            path = root / name / item["file"]
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                raise RuntimeError(f"素材を読み込めません: {path}")
            frames.append(pixmap)
        result[name] = Animation(name, tuple(frames), max(1, int(spec["fps_recommended"])), bool(spec.get("loop", True)))
    return result
