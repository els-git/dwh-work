"""SecretStore keyring: master keys kept as Windows DPAPI files (I02-05).

Every key is a file <key_id>.dpapi in the keyring folder, protected with
Windows DPAPI for the account that runs the backend. New credentials are
encrypted with the active key; older files stay readable with their own key
until it is retired (K07). No key is ever taken from .env, printed or returned.

Server settings:
  DWH_SECRET_KEYRING_DIR     absolute folder with <key_id>.dpapi files
  DWH_SECRET_ACTIVE_KEY_ID   key id used for new credentials
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from dwh_api.secret_dpapi_crypto_provider import load_crypto_from_dpapi_file
from dwh_api.secret_store import SecretStore

KEYRING_DIR_ENV = "DWH_SECRET_KEYRING_DIR"
ACTIVE_KEY_ID_ENV = "DWH_SECRET_ACTIVE_KEY_ID"
KEY_FILE_SUFFIX = ".dpapi"
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

Loader = Callable[[Path], SecretStore]


class KeyringUnavailable(Exception):
    """Sanitized failure: never contains key material or file contents."""


def valid_key_id(key_id: object) -> bool:
    return isinstance(key_id, str) and bool(KEY_ID_RE.fullmatch(key_id)) and key_id not in (".", "..")


def key_file(directory: Path, key_id: str) -> Path:
    if not valid_key_id(key_id):
        raise KeyringUnavailable("Invalid key id")
    return directory / (key_id + KEY_FILE_SUFFIX)


def keyring_directory(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    value = source.get(KEYRING_DIR_ENV, "")
    directory = Path(value) if value else None
    if directory is None or not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
        raise KeyringUnavailable("Keyring unavailable")
    return directory


def load_key(key_id: str, *, environ: Mapping[str, str] | None = None,
             loader: Loader | None = None) -> SecretStore:
    """Crypto core for a given key id; fail closed on any problem (K04)."""
    directory = keyring_directory(environ)
    path = key_file(directory, key_id)
    try:
        return (loader or load_crypto_from_dpapi_file)(path)
    except Exception:
        raise KeyringUnavailable("Master key unavailable") from None


def load_active_key(*, environ: Mapping[str, str] | None = None,
                    loader: Loader | None = None) -> tuple[SecretStore, str]:
    """Crypto core and non-secret id of the active key."""
    source = os.environ if environ is None else environ
    key_id = source.get(ACTIVE_KEY_ID_ENV, "")
    if not valid_key_id(key_id):
        raise KeyringUnavailable("Active key not configured")
    return load_key(key_id, environ=source, loader=loader), key_id
