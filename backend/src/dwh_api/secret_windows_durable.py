"""Fail-closed Windows directory flush barrier for synthetic SecretStore files.

This is not an ACL/key/audit/recovery solution and does not authorize ACTIVE.
A successful FlushFileBuffers proves the API acknowledged the flush request,
not recovery from hardware power loss. No production wiring.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
from uuid import UUID

from dwh_api.secret_file_store import EncryptedFileSecretStore


class DurabilityNotConfirmed(Exception):
    """Intentionally generic; never exposes paths or secret content."""


def _flush_directory_windows(directory: Path) -> None:
    if os.name != 'nt':
        raise DurabilityNotConfirmed('Durability barrier unavailable')
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                           wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        flush = kernel.FlushFileBuffers
        flush.argtypes = (wintypes.HANDLE,)
        flush.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        handle = create(str(directory), 0xC0000000, 0x7, None, 3, 0x02000000, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise DurabilityNotConfirmed('Durability barrier unavailable')
        try:
            if not flush(handle):
                raise DurabilityNotConfirmed('Durability barrier unavailable')
        finally:
            close(handle)
    except DurabilityNotConfirmed:
        raise
    except Exception:
        raise DurabilityNotConfirmed('Durability barrier unavailable') from None


def _uuid(ref: str) -> str:
    if type(ref) is not str:
        raise DurabilityNotConfirmed('Invalid reference')
    try:
        if str(UUID(ref)) != ref:
            raise ValueError('not canonical')
    except (ValueError, AttributeError, TypeError):
        raise DurabilityNotConfirmed('Invalid reference') from None
    return ref


class WindowsDurableFileCreate:
    """Wrap existing encrypted writer; require directory flush + exact readback.

    Injection of _flush is for unit tests only. Production callers must use
    default OS flush and must separately establish trusted actor and ACL.
    """
    def __init__(self, store: EncryptedFileSecretStore, *, _flush=None):
        if not isinstance(store, EncryptedFileSecretStore):
            raise DurabilityNotConfirmed('Invalid encrypted store')
        self._store = store
        self._flush = _flush if _flush is not None else _flush_directory_windows
        if not callable(self._flush):
            raise DurabilityNotConfirmed('Invalid flush provider')

    def create_and_verify(self, *, secret_ref: str, value: str) -> str:
        ref = _uuid(secret_ref)
        if type(value) is not str or not value:
            raise DurabilityNotConfirmed('Invalid secret value')
        directory = self._store._directory
        path = directory / (ref + '.bin')
        # Never return a success receipt on failure. A failed flush may leave an
        # unactivated encrypted orphan; recovery must reconcile manually.
        try:
            self._store.create(secret_ref=ref, value=value)
            self._flush(directory)
            if path.is_symlink() or not path.is_file():
                raise DurabilityNotConfirmed('Encrypted file unverified')
            data = path.read_bytes()
            if not data:
                raise DurabilityNotConfirmed('Encrypted file unverified')
            # Confirm encryption binding by authenticated decrypt, without
            # returning plaintext or including it in any exception.
            if self._store.read(secret_ref=ref) != value:
                raise DurabilityNotConfirmed('Encrypted file unverified')
            if path.read_bytes() != data:
                raise DurabilityNotConfirmed('Encrypted file unverified')
            return hashlib.sha256(data).hexdigest()
        except Exception:
            raise DurabilityNotConfirmed('Encrypted file not durably verified') from None
