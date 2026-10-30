"""Opt-in verified-session administrative CREATE for approved synthetic tests.

The public entry validates a live DWH session and source scope before loading
any DPAPI key, constructing an admin coordinator, or opening a privileged DB
connection. It does not alter legacy operator entry points or expose an API.
"""
from __future__ import annotations

from pathlib import Path
from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_verified_session_create import verified_session_create
from dwh_api.secret_dpapi_crypto_provider import load_crypto_from_dpapi_file
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_admin_create_integration import compose_admin_synthetic_create


class VerifiedAdminCreateStopped(Exception):
    """Sanitized failure when a pre-write prerequisite cannot be established."""


def create_verified_synthetic_secret(*, session_token: str, request: Request,
                                     readonly_connection, admin_connections,
                                     dpapi_key_file: Path, ciphertext_directory: Path,
                                     synthetic_value_supplier):
    """Execute only after fresh auth/session and scoped source check.

    All operator inputs must be approved, including the source mapping and
    synthetic value. Caller must serialize operations and reconcile any
    partial COMMIT before retry. Does not imply crash/backup recovery.
    """
    # Validate all inert dependencies before examining credentials or touching DB.
    if (not isinstance(request, Request)
            or type(request.source_system_id) is not int
            or request.source_system_id <= 0
            or not isinstance(dpapi_key_file, Path)
            or not isinstance(ciphertext_directory, Path)
            or not callable(readonly_connection)
            or not callable(synthetic_value_supplier)
            or not all(callable(getattr(admin_connections, n, None))
                       for n in ('writer', 'reader'))):
        raise VerifiedAdminCreateStopped('Administrative CREATE prerequisites invalid')

    def _after_verification(_verified_state):
        # This closure is never invoked until the DWH session and scoped
        # permission have been freshly verified by the existing DB contract.
        try:
            crypto = load_crypto_from_dpapi_file(dpapi_key_file)
            store = EncryptedFileSecretStore(crypto=crypto, directory=ciphertext_directory)
            coordinator = compose_admin_synthetic_create(
                connections=admin_connections, encrypted_store=store,
                synthetic_value_supplier=synthetic_value_supplier)
        except Exception:
            raise VerifiedAdminCreateStopped('Protected CREATE prerequisites unavailable') from None
        # Do not wrap post-COMMIT errors: the caller must reconcile first.
        return coordinator.create(request)

    # Uses the production session loader; no caller-controlled loader or
    # AuthState input is exposed here.
    return verified_session_create(
        raw_session_token=session_token, request=request,
        readonly_connection=readonly_connection,
        execute_authorized_create=_after_verification)
