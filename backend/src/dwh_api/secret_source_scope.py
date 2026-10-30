"""Disconnected, fail-closed DWH source-scoped authorization for SecretStore.

No HTTP route, filesystem secret operation, or production call site is wired.
A caller must derive source_system_id from a trusted server-side mapping;
never take it from a client-supplied secret_ref. The connection factory must
return a DWH read-only connection (e.g. connect_readonly(get_api_config())).
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing

_PERMISSION = "ADMIN_INTEGRATION_MANAGE"
_RESOURCE_TYPE = "INTEGRATION_SOURCE_SYSTEM"


def source_resource_code(source_system_id: int) -> str:
    """Canonical resource code for a server-validated bigint source ID."""
    if (isinstance(source_system_id, bool) or not isinstance(source_system_id, int)):
        raise ValueError("Invalid source identifier")
    if not 0 < source_system_id <= 9223372036854775807:
        raise ValueError("Invalid source identifier")
    return f"SOURCE_SYSTEM_{source_system_id}"


def authorize_source_secret_create(
    trusted_auth_state: object,
    source_system_id: int,
    *,
    readonly_connection: Callable[[], object],
) -> bool:
    """Check the source and scoped permission, returning False on any uncertainty.

    Requires trusted AuthState and a server-resolved source ID. It does not
    authorize plaintext READ or replace the durable audit/PENDING protocol.
    """
    try:
        resource_code = source_resource_code(source_system_id)
        if getattr(trusted_auth_state, "authenticated", None) is not True:
            return False
        user = getattr(trusted_auth_state, "user", None)
        if user is None or getattr(user, "is_system", None) is not False:
            return False
        if getattr(user, "must_change_password", None) is not False:
            return False
        user_id = getattr(user, "user_id", None)
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            return False
        if not 0 < user_id <= 9223372036854775807:
            return False
        if not callable(readonly_connection):
            return False

        with closing(readonly_connection()) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SHOW transaction_read_only")
                    mode_row = cur.fetchone()
                    if mode_row is None or mode_row["transaction_read_only"] != "on":
                        return False
                    cur.execute(
                        "SELECT is_active FROM meta.source_system "
                        "WHERE source_system_id = %s",
                        (source_system_id,),
                    )
                    source = cur.fetchone()
                    if source is None or source["is_active"] is not True:
                        return False
                    cur.execute(
                        "SELECT app.has_scoped_permission(%s, %s, %s, %s) "
                        "AS allowed",
                        (user_id, _PERMISSION, _RESOURCE_TYPE, resource_code),
                    )
                    result = cur.fetchone()
                    return result is not None and result["allowed"] is True
            finally:
                conn.rollback()
    except Exception:
        return False
