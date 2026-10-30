from __future__ import annotations

import hashlib
import re
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
from pydantic import BaseModel, Field

from dwh_api.auth import SESSION_COOKIE_NAME
from dwh_api.config import get_api_config
from dwh_api.db import connect_control


router = APIRouter(
    prefix="/api/admin/roles",
    tags=["Administration - Roles"],
)

config = get_api_config()

ROLE_CODE_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*$"
)


class RoleCatalogItem(BaseModel):
    role_id: int
    role_code: str
    role_name: str
    description: str | None = None

    is_active: bool
    is_system: bool

    created_at: datetime
    updated_at: datetime

    assigned_user_count: int
    permission_count: int
    resource_count: int

    can_manage: bool


class RoleCreateRequest(BaseModel):
    role_code: str = Field(
        min_length=1,
        max_length=64,
    )

    role_name: str = Field(
        min_length=1,
        max_length=200,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )


class RoleUpdateRequest(BaseModel):
    # PATCH semantics:
    # omitted field = preserve current value
    # explicit null for description = clear current value
    role_name: str | None = Field(
        default=None,
        max_length=200,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )


def _normalize_role_code(
    value: str,
) -> str:
    code = value.strip().upper()

    if not ROLE_CODE_RE.fullmatch(
        code
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid role code. "
                "Use A-Z, 0-9 and underscore; "
                "first character must be A-Z."
            ),
        )

    if code in {
        "SUPERADMIN",
        "ADMIN",
        "USER",
    }:
        raise HTTPException(
            status_code=422,
            detail="Role code is reserved",
        )

    return code


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
        "role administration access denied"
        in lowered
        or
        "admin_role_manage permission required"
        in lowered
        or
        "system role cannot be modified"
        in lowered
        or
        "system role cannot be enabled or disabled"
        in lowered
    ):
        raise HTTPException(
            status_code=403,
            detail=message,
        ) from exc

    if "role does not exist" in lowered:
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc

    if (
        "role code already exists"
        in lowered
        or
        "role name already exists"
        in lowered
        or
        "unique constraint"
        in lowered
    ):
        raise HTTPException(
            status_code=409,
            detail=message,
        ) from exc

    if (
        "invalid role code"
        in lowered
        or
        "role code is reserved"
        in lowered
        or
        "role name is required"
        in lowered
        or
        "role id is required"
        in lowered
        or
        "active state is required"
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
        FROM app.role_catalog_list(%s)
        WHERE role_id = %s
        """,
        (
            token_hash,
            role_id,
        ),
    )

    return cur.fetchone()


def _require_existing_role(
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


@router.get(
    "",
    response_model=list[RoleCatalogItem],
)
def list_roles(
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
                    FROM app.role_catalog_list(%s)
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
        "Unreachable role catalog state"
    )


@router.post(
    "",
    response_model=RoleCatalogItem,
    status_code=201,
)
def create_role(
    request: Request,
    payload: RoleCreateRequest,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    role_code = _normalize_role_code(
        payload.role_code
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

                    cur.execute(
                        """
                        SELECT
                            app.role_catalog_create(
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS role_id
                        """,
                        (
                            token_hash,
                            role_code,
                            payload.role_name,
                            payload.description,
                        ),
                    )

                    created = cur.fetchone()

                    if created is None:
                        raise RuntimeError(
                            "Role creation returned no result"
                        )

                    role_id = created["role_id"]

                    row = _fetch_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Created role could not be read"
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
        "Unreachable role create state"
    )


@router.patch(
    "/{role_id}",
    response_model=RoleCatalogItem,
)
def update_role(
    request: Request,
    payload: RoleUpdateRequest,
    role_id: int = Path(
        ge=1,
    ),
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

                    current = _require_existing_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    role_name = (
                        payload.role_name
                        if "role_name"
                        in payload.model_fields_set
                        else current["role_name"]
                    )

                    description = (
                        payload.description
                        if "description"
                        in payload.model_fields_set
                        else current["description"]
                    )

                    cur.execute(
                        """
                        SELECT
                            app.role_catalog_update(
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS updated
                        """,
                        (
                            token_hash,
                            role_id,
                            role_name,
                            description,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Updated role could not be read"
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
        "Unreachable role update state"
    )


def _set_role_active(
    *,
    request: Request,
    role_id: int,
    is_active: bool,
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

                    _require_existing_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.role_catalog_set_active(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            role_id,
                            is_active,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_role(
                        cur,
                        token_hash,
                        role_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Role state could not be read"
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
        "Unreachable role state"
    )


@router.post(
    "/{role_id}/enable",
    response_model=RoleCatalogItem,
)
def enable_role(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_role_active(
        request=request,
        role_id=role_id,
        is_active=True,
    )


@router.post(
    "/{role_id}/disable",
    response_model=RoleCatalogItem,
)
def disable_role(
    request: Request,
    role_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_role_active(
        request=request,
        role_id=role_id,
        is_active=False,
    )
