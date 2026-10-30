from __future__ import annotations

import hashlib
from contextlib import closing
from datetime import datetime
from uuid import uuid4

import psycopg
from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    Request,
)
from pydantic import BaseModel

from dwh_api.auth import SESSION_COOKIE_NAME
from dwh_api.config import get_api_config
from dwh_api.db import connect_control


router = APIRouter(
    prefix="/api/admin/access",
    tags=["Administration - Access"],
)

config = get_api_config()


class AccessRoleItem(BaseModel):
    role_id: int
    role_code: str
    role_name: str
    description: str | None = None

    is_active: bool
    is_system: bool

    permission_count: int
    resource_count: int

    can_manage: bool


class AccessPermissionItem(BaseModel):
    permission_id: int
    permission_code: str
    permission_name: str
    permission_group: str

    is_active: bool
    is_assignable: bool
    is_assigned: bool

    assigned_at: datetime | None = None


class AccessResourceItem(BaseModel):
    resource_id: int
    resource_type: str
    resource_code: str
    resource_name: str
    description: str | None = None

    is_active: bool
    is_system: bool
    is_assigned: bool

    assigned_at: datetime | None = None


def _session_token_hash(
    request: Request,
) -> str:
    token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _db_message(
    exc: psycopg.Error,
) -> str:
    diag = getattr(
        exc,
        "diag",
        None,
    )

    if diag is not None:
        message = getattr(
            diag,
            "message_primary",
            None,
        )

        if message:
            return str(message)

    text = str(exc).strip()

    if not text:
        return "Database operation failed"

    return text.splitlines()[0]


def _raise_database_error(
    exc: psycopg.Error,
) -> None:
    message = _db_message(
        exc
    )

    lowered = message.lower()

    if (
        "authentication required"
        in lowered
        or
        "invalid authentication session"
        in lowered
    ):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        ) from exc

    if (
        "access administration denied"
        in lowered
        or
        "admin_access_manage permission required"
        in lowered
        or
        "system role access cannot be modified"
        in lowered
        or
        "protected permission cannot be assigned"
        in lowered
    ):
        raise HTTPException(
            status_code=403,
            detail=message,
        ) from exc

    if (
        "role does not exist"
        in lowered
        or
        "permission does not exist"
        in lowered
        or
        "resource does not exist"
        in lowered
    ):
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc

    if (
        "role id is required"
        in lowered
        or
        "permission id is required"
        in lowered
        or
        "resource id is required"
        in lowered
        or
        "assignment state is required"
        in lowered
        or
        "inactive permission cannot be assigned"
        in lowered
        or
        "inactive resource cannot be assigned"
        in lowered
    ):
        raise HTTPException(
            status_code=422,
            detail=message,
        ) from exc

    raise


def _set_request_context(
    cur,
    request_id: str,
) -> None:
    cur.execute(
        """
        SELECT set_config(
            'dwh.request_id',
            %s,
            true
        )
        """,
        (
            request_id,
        ),
    )


def _fetch_role(
    cur,
    token_hash: str,
    role_id: int,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.access_role_list(%s)
        WHERE role_id = %s
        """,
        (
            token_hash,
            role_id,
        ),
    )

    return cur.fetchone()


def _require_role(
    cur,
    token_hash: str,
    role_id: int,
) -> dict:
    row = _fetch_role(
        cur,
        token_hash,
        role_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Role does not exist",
        )

    return row


def _fetch_permission(
    cur,
    token_hash: str,
    role_id: int,
    permission_id: int,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.access_role_permission_list(
            %s,
            %s
        )
        WHERE permission_id = %s
        """,
        (
            token_hash,
            role_id,
            permission_id,
        ),
    )

    return cur.fetchone()


def _fetch_resource(
    cur,
    token_hash: str,
    role_id: int,
    resource_id: int,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.access_role_resource_list(
            %s,
            %s
        )
        WHERE resource_id = %s
        """,
        (
            token_hash,
            role_id,
            resource_id,
        ),
    )

    return cur.fetchone()


@router.get(
    "/roles",
    response_model=list[AccessRoleItem],
)
def list_access_roles(
    request: Request,
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.access_role_list(%s)
                    ORDER BY
                        CASE
                            WHEN is_system THEN 1
                            ELSE 2
                        END,
                        role_code
                    """,
                    (
                        token_hash,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable access role list state"
    )


@router.get(
    "/roles/{role_id}/permissions",
    response_model=list[AccessPermissionItem],
)
def list_role_permissions(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                _require_role(
                    cur,
                    token_hash,
                    role_id,
                )

                cur.execute(
                    """
                    SELECT *
                    FROM app.access_role_permission_list(
                        %s,
                        %s
                    )
                    ORDER BY
                        permission_group,
                        permission_code
                    """,
                    (
                        token_hash,
                        role_id,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable access permission list state"
    )


@router.get(
    "/roles/{role_id}/resources",
    response_model=list[AccessResourceItem],
)
def list_role_resources(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                _require_role(
                    cur,
                    token_hash,
                    role_id,
                )

                cur.execute(
                    """
                    SELECT *
                    FROM app.access_role_resource_list(
                        %s,
                        %s
                    )
                    ORDER BY
                        resource_type,
                        resource_code
                    """,
                    (
                        token_hash,
                        role_id,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable access resource list state"
    )


def _set_permission(
    *,
    request: Request,
    role_id: int,
    permission_id: int,
    is_assigned: bool,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    _require_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    current = _fetch_permission(
                        cur,
                        token_hash,
                        role_id,
                        permission_id,
                    )

                    if current is None:
                        raise HTTPException(
                            status_code=404,
                            detail=(
                                "Assignable permission "
                                "does not exist"
                            ),
                        )

                    cur.execute(
                        """
                        SELECT
                            app.access_role_permission_set(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            role_id,
                            permission_id,
                            is_assigned,
                            (
                                "Access API grant"
                                if is_assigned
                                else "Access API revoke"
                            ),
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_permission(
                        cur,
                        token_hash,
                        role_id,
                        permission_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Permission state "
                            "could not be read"
                        )

                conn.commit()

                return row

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable permission state"
    )


@router.post(
    "/roles/{role_id}/permissions/"
    "{permission_id}/grant",
    response_model=AccessPermissionItem,
)
def grant_role_permission(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
    permission_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_permission(
        request=request,
        role_id=role_id,
        permission_id=permission_id,
        is_assigned=True,
    )


@router.post(
    "/roles/{role_id}/permissions/"
    "{permission_id}/revoke",
    response_model=AccessPermissionItem,
)
def revoke_role_permission(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
    permission_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_permission(
        request=request,
        role_id=role_id,
        permission_id=permission_id,
        is_assigned=False,
    )


def _set_resource(
    *,
    request: Request,
    role_id: int,
    resource_id: int,
    is_assigned: bool,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    _require_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    current = _fetch_resource(
                        cur,
                        token_hash,
                        role_id,
                        resource_id,
                    )

                    if current is None:
                        raise HTTPException(
                            status_code=404,
                            detail="Resource does not exist",
                        )

                    cur.execute(
                        """
                        SELECT
                            app.access_role_resource_set(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            role_id,
                            resource_id,
                            is_assigned,
                            (
                                "Access API grant"
                                if is_assigned
                                else "Access API revoke"
                            ),
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_resource(
                        cur,
                        token_hash,
                        role_id,
                        resource_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Resource state "
                            "could not be read"
                        )

                conn.commit()

                return row

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable resource state"
    )


@router.post(
    "/roles/{role_id}/resources/"
    "{resource_id}/grant",
    response_model=AccessResourceItem,
)
def grant_role_resource(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
    resource_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_resource(
        request=request,
        role_id=role_id,
        resource_id=resource_id,
        is_assigned=True,
    )


@router.post(
    "/roles/{role_id}/resources/"
    "{resource_id}/revoke",
    response_model=AccessResourceItem,
)
def revoke_role_resource(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
    resource_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_resource(
        request=request,
        role_id=role_id,
        resource_id=resource_id,
        is_assigned=False,
    )


# ============================================================
# User <-> Business Role administration
# ============================================================


class AccessUserItem(BaseModel):
    user_id: int
    username: str

    display_name: str | None = None
    email: str | None = None

    account_level: str
    auth_provider: str

    is_active: bool
    is_system: bool

    business_role_count: int
    can_manage_roles: bool


class AccessUserRoleItem(BaseModel):
    role_id: int
    role_code: str
    role_name: str
    description: str | None = None

    role_is_active: bool
    role_is_system: bool

    is_assigned: bool
    assigned_at: datetime | None = None


def _raise_user_role_database_error(
    exc: psycopg.Error,
) -> None:
    message = _db_message(
        exc
    )

    lowered = message.lower()

    if (
        "authentication required" in lowered
        or "invalid authentication session" in lowered
    ):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        ) from exc

    if (
        "access administration denied" in lowered
        or "admin_access_manage permission required" in lowered
        or "administrator access required" in lowered
        or "system user business roles cannot be modified" in lowered
        or "system role cannot be assigned as a business role" in lowered
    ):
        raise HTTPException(
            status_code=403,
            detail=message,
        ) from exc

    if (
        "user does not exist" in lowered
        or "role does not exist" in lowered
    ):
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc

    if (
        "business roles may only be assigned to user accounts" in lowered
        or "inactive user cannot receive a business role" in lowered
        or "inactive role cannot be assigned" in lowered
        or "user id is required" in lowered
        or "role id is required" in lowered
        or "assignment state is required" in lowered
    ):
        raise HTTPException(
            status_code=422,
            detail=message,
        ) from exc

    raise HTTPException(
        status_code=500,
        detail="Database operation failed",
    ) from exc


def _fetch_access_user(
    cur,
    token_hash: str,
    user_id: int,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.access_user_list(%s)
        WHERE user_id = %s
        """,
        (
            token_hash,
            user_id,
        ),
    )

    return cur.fetchone()


def _require_access_user(
    cur,
    token_hash: str,
    user_id: int,
) -> dict:
    row = _fetch_access_user(
        cur,
        token_hash,
        user_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="User does not exist",
        )

    return row


def _fetch_access_user_role(
    cur,
    token_hash: str,
    user_id: int,
    role_id: int,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.access_user_role_list(
            %s,
            %s
        )
        WHERE role_id = %s
        """,
        (
            token_hash,
            user_id,
            role_id,
        ),
    )

    return cur.fetchone()


@router.get(
    "/users",
    response_model=list[AccessUserItem],
)
def list_access_users(
    request: Request,
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.access_user_list(%s)
                    """,
                    (
                        token_hash,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_user_role_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable access user list state"
    )


@router.get(
    "/users/{user_id}/roles",
    response_model=list[AccessUserRoleItem],
)
def list_access_user_roles(
    request: Request,
    user_id: int = Path(
        ge=1,
    ),
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                _require_access_user(
                    cur,
                    token_hash,
                    user_id,
                )

                cur.execute(
                    """
                    SELECT *
                    FROM app.access_user_role_list(
                        %s,
                        %s
                    )
                    """,
                    (
                        token_hash,
                        user_id,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_user_role_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable access user role list state"
    )


def _set_access_user_role(
    *,
    request: Request,
    user_id: int,
    role_id: int,
    is_assigned: bool,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    _require_access_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    current = _fetch_access_user_role(
                        cur,
                        token_hash,
                        user_id,
                        role_id,
                    )

                    if current is None:
                        raise HTTPException(
                            status_code=404,
                            detail="Business role does not exist",
                        )

                    cur.execute(
                        """
                        SELECT
                            app.access_user_role_set(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            user_id,
                            role_id,
                            is_assigned,
                            (
                                "Access API user role grant"
                                if is_assigned
                                else
                                "Access API user role revoke"
                            ),
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_access_user_role(
                        cur,
                        token_hash,
                        user_id,
                        role_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "User role state "
                            "could not be read"
                        )

                conn.commit()

                return row

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_user_role_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable user role state"
    )


@router.post(
    "/users/{user_id}/roles/"
    "{role_id}/grant",
    response_model=AccessUserRoleItem,
)
def grant_access_user_role(
    request: Request,
    user_id: int = Path(
        ge=1,
    ),
    role_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_access_user_role(
        request=request,
        user_id=user_id,
        role_id=role_id,
        is_assigned=True,
    )


@router.post(
    "/users/{user_id}/roles/"
    "{role_id}/revoke",
    response_model=AccessUserRoleItem,
)
def revoke_access_user_role(
    request: Request,
    user_id: int = Path(
        ge=1,
    ),
    role_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_access_user_role(
        request=request,
        user_id=user_id,
        role_id=role_id,
        is_assigned=False,
    )