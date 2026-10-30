"""Fail-closed authorization wrapper for the disconnected administrative CREATE.

The DWH AuthState MUST originate from a trusted server verification path. A
caller-provided dict or fake AuthState is not trustworthy merely by shape.
This wrapper has no default permission or permissive resource guard.
"""
from __future__ import annotations

from dwh_api.secret_create_coordinator import Request
from dwh_api.secret_dwh_identity_adapter import create_human_secret_context


class AdminAuthorizationStopped(Exception):
    pass


def authorized_admin_create(*, request: Request, trusted_auth_state: object,
                            create_permission_code: str, resource_guard,
                            coordinator_factory):
    """Authorize before constructing any coordinator/connecting to PostgreSQL.

    coordinator_factory must be controlled by the trusted operator, and only
    construct a coordinator without side effects. The caller must verify the
    DWH resource and existing permission code (not supplied by API clients).
    """
    try:
        if not isinstance(request, Request):
            raise ValueError()
        if not callable(coordinator_factory) or not callable(resource_guard):
            raise ValueError()
        context = create_human_secret_context(
            trusted_auth_state,
            create_permission_code=create_permission_code,
            resource_guard=resource_guard,
        )
        if context.authorize(context.actor_id, "CREATE", request.secret_ref) is not True:
            raise ValueError()
    except Exception:
        raise AdminAuthorizationStopped("Administrative CREATE not authorized") from None
    # The trust and freshness of AuthState are established by the outer operator.
    # No unverified actor-supplied permission or resource is accepted by default.
    try:
        coordinator = coordinator_factory()
        return coordinator.create(request)
    except Exception:
        raise AdminAuthorizationStopped("Administrative CREATE incomplete; reconcile state") from None
