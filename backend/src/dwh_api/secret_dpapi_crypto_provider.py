"""Windows CurrentUser DPAPI key loader, opt-in for SecretStore.

No new key generation, storage, ACL edits, DB connections or production wiring.
Only an existing trusted key file is read; refusal is sanitized. Operators must
separately define backup/rotation and service Windows identity.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from dwh_api.secret_store import SecretStore


class DpapiKeyUnavailable(Exception):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _win_unprotect(blob: bytes) -> bytes:
    if os.name != "nt":
        raise DpapiKeyUnavailable("Protected master key unavailable")
    source_buffer = ctypes.create_string_buffer(blob)
    source = _Blob(len(blob), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        unprotect = crypt.CryptUnprotectData
        unprotect.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        unprotect.restype = wintypes.BOOL
        if not unprotect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise DpapiKeyUnavailable("Protected master key unavailable")
        if result.cbData != 32 or not result.pbData:
            raise DpapiKeyUnavailable("Protected master key unavailable")
        return bytes(ctypes.string_at(result.pbData, result.cbData))
    finally:
        if result.pbData:
            try:
                ctypes.memset(result.pbData, 0, result.cbData)
                free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
                free.argtypes = [ctypes.c_void_p]
                free.restype = ctypes.c_void_p
                free(ctypes.cast(result.pbData, ctypes.c_void_p))
            except Exception:
                pass
        ctypes.memset(source_buffer, 0, len(source_buffer))


def load_crypto_from_dpapi_file(path: Path) -> SecretStore:
    """Load existing 32-byte CurrentUser DPAPI blob, fail closed on all errors.

    The caller must provide a trusted path, same-Windows-user access, and
    independently audited ACLs; no raw key or plaintext is printed or returned.
    """
    try:
        if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError()
        if path.stat().st_size < 16 or path.stat().st_size > 8192:
            raise ValueError()
        protected = path.read_bytes()
        raw = _win_unprotect(protected)
        try:
            if len(raw) != 32:
                raise ValueError()
            return SecretStore(raw)
        finally:
            # bytes are immutable; avoid saving the key and keep lifetime minimal.
            del raw
    except Exception:
        raise DpapiKeyUnavailable("Protected master key unavailable") from None
