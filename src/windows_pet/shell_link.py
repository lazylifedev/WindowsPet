"""Read-only Windows Shell Link resolution.

The resolver loads a .lnk through the native Shell Link COM API.  It never
invokes the shortcut, and shortcuts carrying arguments or a working directory
are deliberately rejected by returning ``None``.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


_CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSIST_FILE = "{0000010b-0000-0000-C000-000000000046}"
_STGM_READ = 0x00000000
_COINIT_MULTITHREADED = 0x0


def _guid(value: str):
    guid = GUID()
    if ctypes.windll.ole32.CLSIDFromString(value, ctypes.byref(guid)) != 0:
        raise OSError("invalid_guid")
    return guid


class GUID(ctypes.Structure):
    _fields_ = (("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8))


def _method(pointer, index, result, *arguments):
    address = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0]
    vtable = ctypes.cast(address, ctypes.POINTER(ctypes.c_void_p))
    prototype = ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)
    return prototype(vtable[index])


def _release(pointer) -> None:
    if pointer:
        _method(pointer, 2, wintypes.ULONG)(pointer)


def resolve_shortcut(path: str | os.PathLike[str]) -> str | None:
    """Return a safe local target from a .lnk, or ``None`` if unsafe/unreadable."""
    if os.name != "nt" or Path(path).suffix.casefold() != ".lnk":
        return None
    shell_link = ctypes.c_void_p()
    persist_file = ctypes.c_void_p()
    initialized = False
    try:
        if ctypes.windll.ole32.CoInitializeEx(None, _COINIT_MULTITHREADED) not in (0, 1):
            return None
        initialized = True
        create = ctypes.windll.ole32.CoCreateInstance
        create.argtypes = (ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        create.restype = wintypes.HRESULT
        if create(ctypes.byref(_guid(_CLSID_SHELL_LINK)), None, 1,
                  ctypes.byref(_guid(_IID_ISHELL_LINK_W)), ctypes.byref(shell_link)) != 0:
            return None
        query = _method(shell_link, 0, wintypes.HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        if query(shell_link, ctypes.byref(_guid(_IID_IPERSIST_FILE)), ctypes.byref(persist_file)) != 0:
            return None
        load = _method(persist_file, 5, wintypes.HRESULT, wintypes.LPCWSTR, wintypes.DWORD)
        if load(persist_file, str(path), _STGM_READ) != 0:
            return None
        get_path = _method(shell_link, 3, wintypes.HRESULT, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        get_arguments = _method(shell_link, 10, wintypes.HRESULT, wintypes.LPWSTR, ctypes.c_int)
        get_working_directory = _method(shell_link, 8, wintypes.HRESULT, wintypes.LPWSTR, ctypes.c_int)
        target_buffer = ctypes.create_unicode_buffer(32768)
        arguments_buffer = ctypes.create_unicode_buffer(32768)
        working_buffer = ctypes.create_unicode_buffer(32768)
        if get_path(shell_link, target_buffer, len(target_buffer), None, 0) != 0:
            return None
        if get_arguments(shell_link, arguments_buffer, len(arguments_buffer)) != 0:
            return None
        if get_working_directory(shell_link, working_buffer, len(working_buffer)) != 0:
            return None
        if arguments_buffer.value or working_buffer.value:
            return None
        return target_buffer.value or None
    except (OSError, AttributeError, TypeError, ValueError):
        return None
    finally:
        _release(persist_file)
        _release(shell_link)
        if initialized:
            ctypes.windll.ole32.CoUninitialize()
