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
from pydantic import (
    BaseModel,
    Field,
    SecretStr,
)

from dwh_api.auth import SESSION_COOKIE_NAME
from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.security_password import hash_password, validate_new_password


router = APIRouter(
    prefix="/api/admin/users",
    tags=["Administration - Users"],
)

config = get_api_config()


ACCOUNT_LEVELS = {
    "ADMIN",
    "USER",
}

AUTH_PROVIDERS = {
    "LOCAL",
    "LDAP",
    "AD",
    "OIDC",
}


class UserCatalogItem(BaseModel):
    user_id: int
    username: str

    display_name: str | None = None
    email: str | None = None

    account_level: str
    auth_provider: str
    external_subject: str | None = None

    is_active: bool
    is_system: bool
    must_change_password: bool

    created_at: datetime
    updated_at: datetime

    business_role_count: int
    can_manage: bool
    can_reset_password: bool


class UserCreateRequest(BaseModel):
    username: str = Field(
        min_length=1,
        max_length=128,
    )

    display_name: str | None = Field(
        default=None,
        max_length=200,
    )

    email: str | None = Field(
        default=None,
        max_length=320,
    )

    account_level: str = Field(
        default="USER",
        min_length=1,
        max_length=32,
    )

    auth_provider: str = Field(
        default="LOCAL",
        min_length=1,
        max_length=32,
    )

    # SecretStr prevents accidental representation of the
    # plaintext password in normal model repr/debug output.
    password: SecretStr | None = None

    external_subject: str | None = Field(
        default=None,
        max_length=512,
    )

    must_change_password: bool = True


class UserUpdateRequest(BaseModel):
    # PATCH semantics:
    # omitted field = preserve current value
    # explicit null = clear current value
    display_name: str | None = Field(
        default=None,
        max_length=200,
    )

    email: str | None = Field(
        default=None,
        max_length=320,
    )


class UserAccountLevelRequest(BaseModel):
    account_level: str = Field(
        min_length=1,
        max_length=32,
    )


class UserPasswordResetRequest(BaseModel):
    password: SecretStr


def _normalize_account_level(
    value: str,
) -> str:
    level = value.strip().upper()

    if level not in ACCOUNT_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid account level. "
                "Allowed values: ADMIN, USER."
            ),
        )

    return level


def _normalize_auth_provider(
    value: str,
) -> str:
    provider = value.strip().upper()

    if provider not in AUTH_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid authentication provider. "
                "Allowed values: "
                "LOCAL, LDAP, AD, OIDC."
            ),
        )

    return provider


def _normalize_username(
    value: str,
) -> str:
    username = value.strip()

    if not username:
        raise HTTPException(
            status_code=422,
            detail="Username is required",
        )

    return username


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
        "user administration access denied"
        in lowered
        or
        "admin_user_manage permission required"
        in lowered
        or
        "user management denied"
        in lowered
        or
        "account level assignment denied"
        in lowered
        or
        "user password reset denied"
        in lowered
        or
        "system account password reset is server-side only"
        in lowered
    ):
        raise HTTPException(
            status_code=403,
            detail=message,
        ) from exc

    if "user does not exist" in lowered:
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc

    if (
        "username already exists"
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
        "username is required"
        in lowered
        or
        "username is reserved"
        in lowered
        or
        "invalid account level"
        in lowered
        or
        "invalid authentication provider"
        in lowered
        or
        "local account requires password hash"
        in lowered
        or
        "external authentication account cannot store local password hash"
        in lowered
        or
        "password reset is available only for local accounts"
        in lowered
        or
        "password hash is required"
        in lowered
        or
        "invalid local password hash"
        in lowered
    ):
        raise HTTPException(
            status_code=422,
            detail=message,
        ) from exc

    # Invalid LOCAL password hash should never be reachable
    # from a correct API request because this module creates
    # the hash itself using security_password.hash_password().
    # Leave unexpected DB errors unmasked for the global
    # server error handling path.
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


def _with_user_capabilities(
    row: dict | None,
) -> dict | None:
    if row is None:
        return None

    result = dict(row)

    result["can_reset_password"] = bool(
        result.get("can_manage")
        and result.get("auth_provider") == "LOCAL"
        and result.get("is_system") is not True
        and result.get("account_level") != "SUPERADMIN"
    )

    return result


def _fetch_user(
    cur,
    token_hash: str,
    user_id: int,
) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM app.user_catalog_list(%s)
        WHERE user_id = %s
        """,
        (
            token_hash,
            user_id,
        ),
    )

    return _with_user_capabilities(
        cur.fetchone()
    )


def _require_existing_user(
    cur,
    token_hash: str,
    user_id: int,
) -> dict:
    row = _fetch_user(
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


@router.get(
    "",
    response_model=list[UserCatalogItem],
)
def list_users(
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
                    FROM app.user_catalog_list(%s)
                    ORDER BY
                        CASE account_level
                            WHEN 'SUPERADMIN' THEN 1
                            WHEN 'ADMIN' THEN 2
                            ELSE 3
                        END,
                        lower(username)
                    """,
                    (
                        token_hash,
                    ),
                )

                rows = cur.fetchall()

                return [
                    _with_user_capabilities(row)
                    for row in rows
                ]

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable user catalog state"
    )


@router.post(
    "",
    response_model=UserCatalogItem,
    status_code=201,
)
def create_user(
    request: Request,
    payload: UserCreateRequest,
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    username = _normalize_username(
        payload.username
    )

    account_level = _normalize_account_level(
        payload.account_level
    )

    auth_provider = _normalize_auth_provider(
        payload.auth_provider
    )

    password_hash: str | None = None
    external_subject = payload.external_subject
    must_change_password = (
        payload.must_change_password
    )

    if auth_provider == "LOCAL":
        if payload.password is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "LOCAL account requires "
                    "initial password"
                ),
            )

        plaintext_password = (
            payload.password.get_secret_value()
        )

        if not plaintext_password:
            raise HTTPException(
                status_code=422,
                detail=(
                    "LOCAL account requires "
                    "initial password"
                ),
            )

        password_errors = validate_new_password(
            plaintext_password,
            username,
        )

        if password_errors:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "PASSWORD_POLICY_FAILED",
                    "message": "Password does not meet policy requirements",
                    "errors": password_errors,
                },
            )

        password_hash = hash_password(
            plaintext_password
        )

        # Do not keep the plaintext value around longer
        # than needed.
        plaintext_password = ""

        # LOCAL identity does not use an external subject.
        external_subject = None

    else:
        if payload.password is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "External authentication account "
                    "must not include a local password"
                ),
            )

        password_hash = None
        must_change_password = False

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
                            app.user_catalog_create(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS user_id
                        """,
                        (
                            token_hash,
                            username,
                            payload.display_name,
                            payload.email,
                            account_level,
                            auth_provider,
                            password_hash,
                            external_subject,
                            must_change_password,
                        ),
                    )

                    created = cur.fetchone()

                    if created is None:
                        raise RuntimeError(
                            "User creation returned no result"
                        )

                    user_id = created["user_id"]

                    row = _fetch_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Created user could not be read"
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
        "Unreachable user create state"
    )


@router.patch(
    "/{user_id}",
    response_model=UserCatalogItem,
)
def update_user(
    request: Request,
    payload: UserUpdateRequest,
    user_id: int = Path(
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

                    current = _require_existing_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    display_name = (
                        payload.display_name
                        if "display_name"
                        in payload.model_fields_set
                        else current["display_name"]
                    )

                    email = (
                        payload.email
                        if "email"
                        in payload.model_fields_set
                        else current["email"]
                    )

                    cur.execute(
                        """
                        SELECT
                            app.user_catalog_update(
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS updated
                        """,
                        (
                            token_hash,
                            user_id,
                            display_name,
                            email,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Updated user could not be read"
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
        "Unreachable user update state"
    )


def _set_user_active(
    *,
    request: Request,
    user_id: int,
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

                    _require_existing_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.user_catalog_set_active(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            user_id,
                            is_active,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "User state could not be read"
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
        "Unreachable user state"
    )


@router.post(
    "/{user_id}/reset-password",
    response_model=UserCatalogItem,
)
def reset_user_password(
    request: Request,
    payload: UserPasswordResetRequest,
    user_id: int = Path(
        ge=1,
    ),
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    plaintext_password = (
        payload.password.get_secret_value()
    )

    if not plaintext_password:
        raise HTTPException(
            status_code=422,
            detail="Temporary password is required",
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

                    target = _require_existing_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    if not target["can_reset_password"]:
                        raise HTTPException(
                            status_code=403,
                            detail="User password reset denied",
                        )

                    password_errors = validate_new_password(
                        plaintext_password,
                        target["username"],
                    )

                    if password_errors:
                        raise HTTPException(
                            status_code=422,
                            detail={
                                "code": "PASSWORD_POLICY_FAILED",
                                "message": (
                                    "Password does not meet "
                                    "policy requirements"
                                ),
                                "errors": password_errors,
                            },
                        )

                    password_hash = hash_password(
                        plaintext_password
                    )

                    plaintext_password = ""

                    cur.execute(
                        """
                        SELECT
                            app.user_catalog_reset_password(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            user_id,
                            password_hash,
                        ),
                    )

                    changed = cur.fetchone()

                    password_hash = ""

                    if (
                        changed is None
                        or changed["changed"] is not True
                    ):
                        raise RuntimeError(
                            "Password reset returned "
                            "unexpected result"
                        )

                    row = _fetch_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "Reset user could not be read"
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
        "Unreachable password reset state"
    )


@router.post(
    "/{user_id}/enable",
    response_model=UserCatalogItem,
)
def enable_user(
    request: Request,
    user_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_user_active(
        request=request,
        user_id=user_id,
        is_active=True,
    )


@router.post(
    "/{user_id}/disable",
    response_model=UserCatalogItem,
)
def disable_user(
    request: Request,
    user_id: int = Path(
        ge=1,
    ),
) -> dict:
    return _set_user_active(
        request=request,
        user_id=user_id,
        is_active=False,
    )


@router.patch(
    "/{user_id}/account-level",
    response_model=UserCatalogItem,
)
def set_user_account_level(
    request: Request,
    payload: UserAccountLevelRequest,
    user_id: int = Path(
        ge=1,
    ),
) -> dict:
    token_hash = _session_token_hash(
        request
    )

    account_level = _normalize_account_level(
        payload.account_level
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

                    _require_existing_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.user_catalog_set_account_level(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            user_id,
                            account_level,
                        ),
                    )

                    cur.fetchone()

                    row = _fetch_user(
                        cur,
                        token_hash,
                        user_id,
                    )

                    if row is None:
                        raise RuntimeError(
                            "User level could not be read"
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
        "Unreachable user level state"
    )