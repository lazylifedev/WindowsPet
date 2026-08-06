from __future__ import annotations
import os

SERVICE_NAME = "WindowsPet"
USERNAME = "openai_api_key"

def _keyring():
    try:
        import keyring
        if os.name == "nt":
            backend = keyring.get_keyring()
            if not type(backend).__module__.startswith("keyring.backends.Windows"):
                return None
        return keyring
    except ImportError:
        return None

def get_api_key() -> str | None:
    value = os.getenv("OPENAI_API_KEY")
    if value and value.strip(): return value.strip()
    kr = _keyring()
    if kr is None: return None
    try: value = kr.get_password(SERVICE_NAME, USERNAME)
    except Exception: return None
    return value.strip() if value and value.strip() else None

def has_environment_key() -> bool: return bool(os.getenv("OPENAI_API_KEY", "").strip())

def has_stored_key() -> bool:
    kr = _keyring()
    if kr is None: return False
    try: return bool(kr.get_password(SERVICE_NAME, USERNAME))
    except Exception: return False

def is_api_key_configured() -> bool:
    return has_environment_key() or has_stored_key()

def save_api_key(value: str) -> None:
    kr = _keyring()
    if kr is None: raise RuntimeError("Credential Manager が利用できません。keyring をインストールしてください。")
    kr.set_password(SERVICE_NAME, USERNAME, value)

def delete_api_key() -> None:
    kr = _keyring()
    if kr is None: return
    try: kr.delete_password(SERVICE_NAME, USERNAME)
    except Exception: pass
