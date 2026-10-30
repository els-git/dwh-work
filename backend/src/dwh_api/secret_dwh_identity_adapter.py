"""Disconnected adapter from a *trusted* DWH AuthState to SecretAccessBoundary.

Only call with AuthState obtained from the existing require_authenticated /
authorize_api_access dependency on the server.  Never accept AuthState or
permission names supplied by API clients, forms, or query parameters.

This adapter has NO production wiring. It does not create or choose DWH
permission codes: the integration must supply an EXISTING server-approved
permission code and a server-side per-secret resource authorization callback.
It allows a human to CREATE only. READ of plaintext by any human is always
forbidden; a separate audited service identity is required for ETL READ.
It does not implement durable outcome audit or secret lifecycle/rotation.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from dwh_api.secret_access_boundary import SecretOperation

_ACTOR = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)


class DwhSecretIdentityDenied(Exception):
    """Sanitized refusal without identity, secret or permission details."""


class ResourceGuard(Protocol):
    def __call__(self, secret_ref: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class DwhHumanSecretContext:
    actor_id: str = field(repr=False)
    authorize: Callable[[str, SecretOperation, str], bool] = field(repr=False)

    def __repr__(self) -> str:
        return "DwhHumanSecretContext(<identity hidden>)"


def create_human_secret_context(
    trusted_auth_state: object,
    *,
    create_permission_code: str,
    resource_guard: ResourceGuard,
) -> DwhHumanSecretContext:
    """Produce a CREATE-only gate from a trusted, freshly loaded DWH AuthState.

    The caller MUST set create_permission_code to a verified EXISTING DWH
    permission; MUST implement resource_guard using real server-side object
    ownership, and MUST pass a durable audit to SecretAccessBoundary. Until
    these prerequisites exist, do not wire into a live route or executor.
    """
    try:
        if getattr(trusted_auth_state, "authenticated", None) is not True:
            raise DwhSecretIdentityDenied("Secret access denied")
        user = getattr(trusted_auth_state, "user", None)
        if user is None:
            raise DwhSecretIdentityDenied("Secret access denied")
        if getattr(user, "is_system", None) is not False:
            raise DwhSecretIdentityDenied("Secret access denied")
        if getattr(user, "must_change_password", None) is not False:
            raise DwhSecretIdentityDenied("Secret access denied")
        user_id = getattr(user, "user_id", None)
        if isinstance(user_id, bool) or not isinstance(user_id, (str, int)):
            raise DwhSecretIdentityDenied("Secret access denied")
        if isinstance(user_id, int) and user_id <= 0:
            raise DwhSecretIdentityDenied("Secret access denied")
        actor_id = str(user_id)
        if _ACTOR.fullmatch(actor_id) is None:
            raise DwhSecretIdentityDenied("Secret access denied")
        if (
            not isinstance(create_permission_code, str)
            or not create_permission_code
            or len(create_permission_code) > 128
            or not create_permission_code.isascii()
            or not all(ch.isalnum() or ch in "._:-" for ch in create_permission_code)
        ):
            raise DwhSecretIdentityDenied("Secret access denied")
        if not callable(resource_guard):
            raise DwhSecretIdentityDenied("Secret access denied")
        permissions = getattr(trusted_auth_state, "permissions", None)
        if not isinstance(permissions, (tuple, list)):
            raise DwhSecretIdentityDenied("Secret access denied")
        granted = frozenset(
            getattr(permission, "permission_code") for permission in permissions
        )
        has_create_permission = create_permission_code in granted
    except Exception:
        raise DwhSecretIdentityDenied("Secret access denied") from None

    def authorize(actor: str, operation: SecretOperation, secret_ref: str) -> bool:
        if actor != actor_id or operation != "CREATE" or not has_create_permission:
            return False
        try:
            return resource_guard(secret_ref) is True
        except Exception:
            return False

    return DwhHumanSecretContext(actor_id=actor_id, authorize=authorize)
