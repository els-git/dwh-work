from __future__ import annotations

import hashlib
import re
from contextlib import closing
from datetime import datetime
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dwh_api.auth import SESSION_COOKIE_NAME
from dwh_api.config import get_api_config
from dwh_api.db import connect_control


router = APIRouter(
    prefix="/api/admin/modules",
    tags=["Administration - Modules"],
)

config = get_api_config()

MODULE_CODE_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*$"
)


class ModuleCatalogItem(BaseModel):
    resource_id: int
    module_code: str
    module_name: str
    description: str | None = None
    is_active: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class ModuleCreateRequest(BaseModel):
    module_code: str = Field(
        min_length=1,
        max_length=64,
    )

    module_name: str = Field(
        min_length=1,
        max_length=200,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )


class ModuleUpdateRequest(BaseModel):
    module_name: str = Field(
        min_length=1,
        max_length=200,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )


def _normalize_module_code(
    value: str,
) -> str:
    code = value.strip().upper()

    if not MODULE_CODE_RE.fullmatch(
        code
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid module code. "
                "Use A-Z, 0-9 and underscore; "
                "first character must be A-Z."
            ),
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
        "administration access denied"
        in lowered
        or
        "permission required"
        in lowered
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Module administration "
                "access denied"
            ),
        ) from exc

    if "module not found" in lowered:
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc

    if (
        "already exists"
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
        "invalid module code"
        in lowered
        or
        "module name is required"
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


def _fetch_module(
    cur,
    token_hash: str,
    module_code: str,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.module_catalog_list(%s)
        WHERE module_code = %s
        """,
        (
            token_hash,
            module_code,
        ),
    )

    return cur.fetchone()


@router.get(
    "",
    response_model=list[ModuleCatalogItem],
)
def list_modules(
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
                    FROM app.module_catalog_list(%s)
                    ORDER BY module_code
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
        "Unreachable module catalog state"
    )


@router.post(
    "",
    response_model=ModuleCatalogItem,
    status_code=201,
)
def create_module(
    request: Request,
    payload: ModuleCreateRequest,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    module_code = _normalize_module_code(
        payload.module_code
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
                            app.module_catalog_create(
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS resource_id
                        """,
                        (
                            token_hash,
                            module_code,
                            payload.module_name,
                            payload.description,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_module(
                        cur,
                        token_hash,
                        module_code,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Created module "
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
        "Unreachable module create state"
    )


@router.patch(
    "/{module_code}",
    response_model=ModuleCatalogItem,
)
def update_module(
    module_code: str,
    request: Request,
    payload: ModuleUpdateRequest,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    code = _normalize_module_code(
        module_code
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
                            app.module_catalog_update(
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS updated
                        """,
                        (
                            token_hash,
                            code,
                            payload.module_name,
                            payload.description,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_module(
                        cur,
                        token_hash,
                        code,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Updated module "
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
        "Unreachable module update state"
    )


def _set_module_active(
    *,
    request: Request,
    module_code: str,
    is_active: bool,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    code = _normalize_module_code(
        module_code
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
                            app.module_catalog_set_active(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            code,
                            is_active,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_module(
                        cur,
                        token_hash,
                        code,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Module state "
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
        "Unreachable module state"
    )


@router.post(
    "/{module_code}/enable",
    response_model=ModuleCatalogItem,
)
def enable_module(
    module_code: str,
    request: Request,
) -> dict:
    return _set_module_active(
        request=request,
        module_code=module_code,
        is_active=True,
    )


@router.post(
    "/{module_code}/disable",
    response_model=ModuleCatalogItem,
)
def disable_module(
    module_code: str,
    request: Request,
) -> dict:
    return _set_module_active(
        request=request,
        module_code=module_code,
        is_active=False,
    )