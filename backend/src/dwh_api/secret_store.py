"""Isolated authenticated encryption for DWH connector secrets.

This is only the cryptographic core. It does NOT store encrypted material,
manage the master key, grant permissions, audit access, or expose an API.
Do not use it with real credentials until those safeguards are implemented.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"DWH-SS\x01"
_NONCE_SIZE = 12
_KEY_SIZE = 32
_MIN_ENVELOPE_SIZE = len(_MAGIC) + _NONCE_SIZE + 16


class SecretStoreError(Exception):
    """Safe-to-display error with no input, key or ciphertext in its message."""


def generate_secret_ref() -> str:
    """Return an opaque UUID identifier for a secret record."""
    return str(uuid.uuid4())


def decode_master_key(encoded: str) -> bytes:
    """Validate an externally supplied Base64-encoded 256-bit key.

    No key generation, environment access, storage, or logging is performed.
    """
    if not isinstance(encoded, str):
        raise SecretStoreError("Invalid master key encoding")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SecretStoreError("Invalid master key encoding") from None
    if len(key) != _KEY_SIZE:
        raise SecretStoreError("Invalid master key length")
    return key


def _aad(secret_ref: str) -> bytes:
    if not isinstance(secret_ref, str):
        raise SecretStoreError("Invalid secret reference")
    try:
        parsed = uuid.UUID(secret_ref)
    except (ValueError, AttributeError, TypeError):
        raise SecretStoreError("Invalid secret reference") from None
    if str(parsed) != secret_ref:
        raise SecretStoreError("Invalid secret reference")
    return _MAGIC + parsed.bytes


class SecretStore:
    """AES-256-GCM authenticated envelope operations; NO disk or DB access."""

    __slots__ = ("_cipher",)

    def __init__(self, master_key: bytes) -> None:
        if not isinstance(master_key, bytes) or len(master_key) != _KEY_SIZE:
            raise SecretStoreError("Invalid master key")
        self._cipher = AESGCM(master_key)

    def __repr__(self) -> str:
        return "SecretStore(<key hidden>)"

    def encrypt(self, *, secret_ref: str, value: str) -> bytes:
        aad = _aad(secret_ref)
        if not isinstance(value, str) or not value:
            raise SecretStoreError("Secret value must be nonempty text")
        nonce = secrets.token_bytes(_NONCE_SIZE)
        ciphertext = self._cipher.encrypt(nonce, value.encode("utf-8"), aad)
        return _MAGIC + nonce + ciphertext

    def decrypt(self, *, secret_ref: str, envelope: bytes) -> str:
        aad = _aad(secret_ref)
        if not isinstance(envelope, bytes) or len(envelope) < _MIN_ENVELOPE_SIZE:
            raise SecretStoreError("Unable to decrypt secret")
        if not envelope.startswith(_MAGIC):
            raise SecretStoreError("Unable to decrypt secret")
        nonce_start = len(_MAGIC)
        nonce = envelope[nonce_start:nonce_start + _NONCE_SIZE]
        ciphertext = envelope[nonce_start + _NONCE_SIZE:]
        try:
            return self._cipher.decrypt(nonce, ciphertext, aad).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            raise SecretStoreError("Unable to decrypt secret") from None
