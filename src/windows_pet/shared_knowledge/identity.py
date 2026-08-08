from __future__ import annotations

import secrets


def new_installation_evidence_id() -> str:
    """Create a resettable opaque evidence id; never derive it from the device."""
    return f"install-{secrets.token_hex(16)}"
