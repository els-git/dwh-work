"""Unconnected encrypted-file adapter for the DWH SecretStore core.

This module does NOT provision or verify OS ACLs, manage encryption keys,
perform DWH authorization, audit secret use, or expose an endpoint. It must
not be connected to real credentials until those protections are implemented
and independently tested. The directory must exist before construction.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import uuid

from dwh_api.secret_store import SecretStore, SecretStoreError


class SecretFileStoreError(Exception):
    """Failure messages never contain plaintext, ciphertext, or keys."""


def _reference_path(directory: Path, secret_ref: str) -> Path:
    if not isinstance(secret_ref, str):
        raise SecretFileStoreError("Invalid secret reference")
    try:
        parsed = uuid.UUID(secret_ref)
    except (ValueError, TypeError, AttributeError):
        raise SecretFileStoreError("Invalid secret reference") from None
    if str(parsed) != secret_ref:
        raise SecretFileStoreError("Invalid secret reference")
    return directory / (secret_ref + ".bin")


def _check_directory(directory: Path) -> None:
    try:
        if not directory.is_absolute() or not directory.is_dir():
            raise SecretFileStoreError("Secure directory unavailable")
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            raise SecretFileStoreError("Secure directory unavailable")
    except OSError:
        raise SecretFileStoreError("Secure directory unavailable") from None


def _is_plain_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False
    except OSError:
        raise SecretFileStoreError("Secret file unavailable") from None


class EncryptedFileSecretStore:
    """Persist opaque encrypted envelopes; the caller supplies a crypto core.

    The caller MUST independently enforce DWH permissions, auditing, Windows
    filesystem ACLs, and secure external master-key management before use
    outside synthetic tests. Nothing creates a default production directory.
    """

    __slots__ = ("_crypto", "_directory")

    def __init__(self, *, crypto: SecretStore, directory: Path) -> None:
        if not isinstance(crypto, SecretStore):
            raise SecretFileStoreError("Invalid crypto provider")
        if not isinstance(directory, Path):
            raise SecretFileStoreError("Invalid directory")
        _check_directory(directory)
        self._crypto = crypto
        self._directory = directory

    def __repr__(self) -> str:
        return "EncryptedFileSecretStore(<path and key hidden>)"

    def create(self, *, secret_ref: str, value: str) -> None:
        """Create once; refuse existing refs; never write plaintext."""
        target = _reference_path(self._directory, secret_ref)
        _check_directory(self._directory)
        envelope = self._crypto.encrypt(secret_ref=secret_ref, value=value)
        temporary = self._directory / (".pending-" + str(uuid.uuid4()))
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            with os.fdopen(os.open(temporary, flags, 0o600), "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            # Same-directory hard-link creates the final name atomically,
            # and fails rather than overwriting an existing record.
            os.link(temporary, target)
            created = True
        except FileExistsError:
            raise SecretFileStoreError("Secret record already exists") from None
        except OSError:
            raise SecretFileStoreError("Unable to save encrypted secret") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                if not created:
                    raise SecretFileStoreError("Unable to clean temporary file") from None

    def read(self, *, secret_ref: str) -> str:
        """Decrypt only the requested, canonical UUID record."""
        target = _reference_path(self._directory, secret_ref)
        _check_directory(self._directory)
        if not _is_plain_file(target):
            raise SecretFileStoreError("Secret record unavailable")
        try:
            envelope = target.read_bytes()
        except OSError:
            raise SecretFileStoreError("Secret record unavailable") from None
        try:
            return self._crypto.decrypt(secret_ref=secret_ref, envelope=envelope)
        except SecretStoreError:
            raise SecretFileStoreError("Unable to decrypt secret") from None
