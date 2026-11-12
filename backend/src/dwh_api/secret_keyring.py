"""SecretStore keyring: master keys kept as Windows DPAPI files (I02-05) or, on the
Linux server, as systemd credentials (I16, decision C9).

Every key is a file <key_id>.dpapi in the keyring folder, protected with
Windows DPAPI for the account that runs the backend. New credentials are
encrypted with the active key; older files stay readable with their own key
until it is retired (K07). No key is ever taken from .env, printed or returned.

Server settings:
  DWH_SECRET_KEY_PROVIDER    dpapi (default, Windows PC) or systemd-creds (Linux server)
  DWH_SECRET_KEYRING_DIR     dpapi: absolute folder with <key_id>.dpapi files
  DWH_SECRET_ACTIVE_KEY_ID   key id used for new credentials

With systemd-creds the keys are not read from a folder of ours: the service unit
loads every <key_id>.cred with LoadCredentialEncrypted=dwh-key.<key_id>:<file>,
and systemd hands the decrypted key over in $CREDENTIALS_DIRECTORY/dwh-key.<key_id>.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from dwh_api.secret_dpapi_crypto_provider import load_crypto_from_dpapi_file
from dwh_api.secret_store import SecretStore
from dwh_api.secret_systemd_creds_provider import load_crypto_from_credential_file

KEYRING_DIR_ENV = "DWH_SECRET_KEYRING_DIR"
ACTIVE_KEY_ID_ENV = "DWH_SECRET_ACTIVE_KEY_ID"
KEY_PROVIDER_ENV = "DWH_SECRET_KEY_PROVIDER"
CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"
PROVIDER_DPAPI = "dpapi"
PROVIDER_SYSTEMD_CREDS = "systemd-creds"
KEY_FILE_SUFFIX = ".dpapi"
CREDENTIAL_FILE_SUFFIX = ".cred"
CREDENTIAL_PREFIX = "dwh-key."
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

Loader = Callable[[Path], SecretStore]


class KeyringUnavailable(Exception):
    """Sanitized failure: never contains key material or file contents."""


def valid_key_id(key_id: object) -> bool:
    return isinstance(key_id, str) and bool(KEY_ID_RE.fullmatch(key_id)) and key_id not in (".", "..")


def key_file(directory: Path, key_id: str, suffix: str = KEY_FILE_SUFFIX) -> Path:
    if not valid_key_id(key_id):
        raise KeyringUnavailable("Invalid key id")
    return directory / (key_id + suffix)


def credential_name(key_id: str) -> str:
    """Name of the systemd credential that carries a key (bound into the encrypted file)."""
    if not valid_key_id(key_id):
        raise KeyringUnavailable("Invalid key id")
    return CREDENTIAL_PREFIX + key_id


def key_provider(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = (source.get(KEY_PROVIDER_ENV, "") or PROVIDER_DPAPI).strip()
    if value not in (PROVIDER_DPAPI, PROVIDER_SYSTEMD_CREDS):
        raise KeyringUnavailable("Key provider not supported")
    return value


def credentials_directory(environ: Mapping[str, str] | None = None) -> Path:
    """Folder where systemd placed the decrypted credentials of this service."""
    source = os.environ if environ is None else environ
    value = source.get(CREDENTIALS_DIRECTORY_ENV, "")
    directory = Path(value) if value else None
    if directory is None or not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
        raise KeyringUnavailable("Keyring unavailable")
    return directory


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
    if key_provider(environ) == PROVIDER_SYSTEMD_CREDS:
        path = credentials_directory(environ) / credential_name(key_id)
        default_loader: Loader = load_crypto_from_credential_file
    else:
        path = key_file(keyring_directory(environ), key_id)
        default_loader = load_crypto_from_dpapi_file
    try:
        return (loader or default_loader)(path)
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
