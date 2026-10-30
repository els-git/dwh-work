"""Disconnected master-key loader for the DWH SecretStore crypto core.

Only consumes an EXTERNALLY provisioned Base64 AES-256 key. Does not create,
store, rotate, export or log keys. No API, ETL, database, filesystem writes,
service identity, key-manager integration or production secret access here.
The operating environment must secure key injection before production use.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from dwh_api.secret_store import SecretStore, SecretStoreError, decode_master_key

MASTER_KEY_ENV = "DWH_CONNECTOR_MASTER_KEY_B64"


class SecretKeyProviderError(Exception):
    """Sanitized provider failure: never contains key content."""


def load_crypto_from_environment(
    *, environ: Mapping[str, str] | None = None
) -> SecretStore:
    """Fail closed unless a 32-byte master key is supplied by the server.

    With environ=None, uses the current server process environment. Pass an
    explicit mapping during synthetic tests. No .env files or disk are read.
    Returns an initialized crypto core, not the original Base64 key.
    """
    source = os.environ if environ is None else environ
    if not isinstance(source, Mapping):
        raise SecretKeyProviderError("Master key unavailable")
    try:
        encoded = source.get(MASTER_KEY_ENV)
        if not isinstance(encoded, str) or not encoded:
            raise SecretKeyProviderError("Master key unavailable")
        key = decode_master_key(encoded)
        return SecretStore(key)
    except (SecretStoreError, TypeError, ValueError, OSError):
        raise SecretKeyProviderError("Master key unavailable") from None
