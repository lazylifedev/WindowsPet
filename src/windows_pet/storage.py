import json
from pathlib import Path
from PySide6.QtCore import QPoint, QRect

def load_position(path: Path, fallback: QPoint = QPoint(100, 100)) -> QPoint:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return QPoint(int(data["x"]), int(data["y"]))
    except (OSError, ValueError, KeyError, TypeError):
        return QPoint(fallback)

def save_position(path: Path, point: QPoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"x": point.x(), "y": point.y()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def constrain_to_primary(point: QPoint, screen: QRect, size: int = 200) -> QPoint:
    x = min(max(point.x(), screen.left()), screen.right() - size + 1)
    y = min(max(point.y(), screen.top()), screen.bottom() - size + 1)
    return QPoint(x, y)
