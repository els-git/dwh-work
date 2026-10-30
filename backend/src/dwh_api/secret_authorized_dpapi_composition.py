"""Opt-in synthetic SecretStore CREATE with authorization and DPAPI prerequisite.

No API wiring, no implicit DB connection, no writes until create() in trusted
coordinator. The caller is responsible for authenticated AuthState provenance,
resource guard, serialized operations and approved administrative credentials.
"""
from __future__ import annotations

from pathlib import Path
from dwh_api.secret_authorized_admin_create import authorized_admin_create, AdminAuthorizationStopped
from dwh_api.secret_dpapi_crypto_provider import load_crypto_from_dpapi_file
from dwh_api.secret_admin_create_integration import compose_admin_synthetic_create
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_create_coordinator import Request


class AuthorizedCompositionStopped(Exception):
    pass


def authorized_dpapi_synthetic_create(*, request: Request, trusted_auth_state: object,
                                      create_permission_code: str, resource_guard,
                                      connections, dpapi_key_file: Path, ciphertext_directory: Path,
                                      synthetic_value_supplier):
    """Fail closed: authorization -> DPAPI -> composition -> CREATE.

    Do not treat a caller-created AuthState as proof of identity. This is an
    operator-only opt-in interface; existing callers remain unchanged.
    """
    def factory():
        crypto = load_crypto_from_dpapi_file(dpapi_key_file)
        store = EncryptedFileSecretStore(crypto=crypto, directory=ciphertext_directory)
        return compose_admin_synthetic_create(connections=connections,
            encrypted_store=store, synthetic_value_supplier=synthetic_value_supplier)
    try:
        return authorized_admin_create(request=request, trusted_auth_state=trusted_auth_state,
            create_permission_code=create_permission_code, resource_guard=resource_guard,
            coordinator_factory=factory)
    except Exception:
        raise AuthorizedCompositionStopped('Authorized synthetic CREATE incomplete; reconcile state') from None
