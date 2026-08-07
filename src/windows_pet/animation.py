"""Compatibility access to the trusted bundled legacy animation assets."""

from pathlib import Path

from .character_models import CharacterAnimation, CharacterPackageError
from .character_package_loader import load_builtin_default_character


def load_animations(root: Path) -> dict[str, CharacterAnimation]:
    """Keep the historical API without retaining a general-purpose loose loader."""
    try:
        return dict(load_builtin_default_character(root).animations)
    except CharacterPackageError as exc:
        raise RuntimeError(exc.code.value) from exc
