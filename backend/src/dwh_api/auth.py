from __future__ import annotations

import hashlib
import os
import secrets
import uuid

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from typing import Callable

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.security_password import (
    hash_password,
    validate_new_password,
    verify_password,
)


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


SESSION_COOKIE_NAME = "dwh_session"


def _cookie_secure() -> bool:
    value = os.getenv(
        "DWH_COOKIE_SECURE",
        "false",
    )

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _session_ttl_hours() -> int:
    raw_value = os.getenv(
        "DWH_SESSION_TTL_HOURS",
        "8",
    )

    try:
        value = int(raw_value)

    except ValueError as exc:
        raise RuntimeError(
            "DWH_SESSION_TTL_HOURS must be integer"
        ) from exc

    if value < 1 or value > 24:
        raise RuntimeError(
            "DWH_SESSION_TTL_HOURS must be between 1 and 24"
        )

    return value


SESSION_TTL_HOURS = _session_ttl_hours()
COOKIE_SECURE = _cookie_secure()


# Used only to make unknown-user login attempts perform
# approximately the same expensive password operation.
DUMMY_PASSWORD_HASH = hash_password(
    "DWH-DUMMY-AUTH-PASSWORD-NOT-A-REAL-ACCOUNT"
)


class AuthApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )


class LoginRequest(AuthApiModel):
    username: str = Field(
        min_length=1,
        max_length=200,
    )

    password: str = Field(
        min_length=1,
        max_length=512,
    )


class AuthUser(AuthApiModel):
    user_id: int
    username: str
    display_name: str | None = None
    email: str | None = None

    account_level: str
    auth_provider: str

    is_system: bool
    must_change_password: bool

    session_id: int
    session_created_at: datetime
    session_expires_at: datetime


class AuthRole(AuthApiModel):
    role_code: str
    role_name: str


class AuthPermission(AuthApiModel):
    permission_code: str
    permission_name: str
    permission_group: str


class AuthResource(AuthApiModel):
    resource_type: str
    resource_code: str
    resource_name: str


class AuthState(AuthApiModel):
    authenticated: bool

    user: AuthUser | None = None

    roles: list[AuthRole] = Field(
        default_factory=list
    )

    permissions: list[AuthPermission] = Field(
        default_factory=list
    )

    resources: list[AuthResource] = Field(
        default_factory=list
    )


class LogoutResponse(AuthApiModel):
    logged_out: bool


class ChangePasswordRequest(AuthApiModel):
    current_password: str | None = Field(
        default=None,
        max_length=512,
    )

    new_password: str = Field(
        min_length=1,
        max_length=512,
    )


class ChangePasswordResponse(AuthApiModel):
    changed: bool
    auth: AuthState


def _hash_session_token(
    token: str,
) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _new_session_token() -> str:
    return secrets.token_urlsafe(
        48
    )


def _request_id(
    request: Request,
) -> str:
    header_value = request.headers.get(
        "X-Request-ID"
    )

    if header_value:
        value = header_value.strip()

        if value:
            return value[:200]

    return uuid.uuid4().hex


def _client_ip(
    request: Request,
) -> str | None:
    if request.client is None:
        return None

    return request.client.host


def _set_request_context(
    cursor,
    request: Request,
    source: str,
) -> None:
    cursor.execute(
        """
        SELECT set_config(
            'dwh.request_id',
            %s,
            true
        )
        """,
        (
            _request_id(request),
        ),
    )

    cursor.execute(
        """
        SELECT set_config(
            'dwh.security_source',
            %s,
            true
        )
        """,
        (
            source,
        ),
    )


def _build_auth_state(
    session_row: dict,
    roles: list[dict],
    permissions: list[dict],
    resources: list[dict],
) -> AuthState:
    user = AuthUser(
        user_id=session_row["user_id"],
        username=session_row["username"],
        display_name=session_row["display_name"],
        email=session_row["email"],

        account_level=session_row["account_level"],
        auth_provider=session_row["auth_provider"],

        is_system=session_row["is_system"],
        must_change_password=(
            session_row["must_change_password"]
        ),

        session_id=session_row["session_id"],
        session_created_at=session_row["created_at"],
        session_expires_at=session_row["expires_at"],
    )

    return AuthState(
        authenticated=True,
        user=user,

        roles=[
            AuthRole(**row)
            for row in roles
        ],

        permissions=[
            AuthPermission(**row)
            for row in permissions
        ],

        resources=[
            AuthResource(**row)
            for row in resources
        ],
    )


def _load_auth_state_by_hash(
    token_hash: str,
) -> AuthState | None:
    config = get_api_config()

    conn = connect_control(
        config
    )

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT *
                FROM app.auth_get_session(%s)
                """,
                (
                    token_hash,
                ),
            )

            session_row = cursor.fetchone()

            if session_row is None:
                conn.rollback()
                return None


            user_id = session_row[
                "user_id"
            ]


            cursor.execute(
                """
                SELECT
                    role_code,
                    role_name
                FROM app.auth_get_roles(%s)
                """,
                (
                    user_id,
                ),
            )

            roles = cursor.fetchall()


            cursor.execute(
                """
                SELECT
                    permission_code,
                    permission_name,
                    permission_group
                FROM app.auth_get_permissions(%s)
                """,
                (
                    user_id,
                ),
            )

            permissions = cursor.fetchall()


            cursor.execute(
                """
                SELECT
                    resource_type,
                    resource_code,
                    resource_name
                FROM app.auth_get_resources(%s)
                """,
                (
                    user_id,
                ),
            )

            resources = cursor.fetchall()


            conn.rollback()

            return _build_auth_state(
                session_row,
                roles,
                permissions,
                resources,
            )

    finally:
        conn.close()


def _load_auth_state(
    request: Request,
) -> AuthState | None:
    cached = getattr(
        request.state,
        "dwh_auth_state",
        None,
    )

    if cached is not None:
        return cached


    raw_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not raw_token:
        return None


    if len(raw_token) > 512:
        return None


    state = _load_auth_state_by_hash(
        _hash_session_token(
            raw_token
        )
    )


    if state is not None:
        request.state.dwh_auth_state = state


    return state


@router.post(
    "/login",
    response_model=AuthState,
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> AuthState:
    username = payload.username.strip()

    config = get_api_config()

    conn = connect_control(
        config
    )

    raw_token: str | None = None
    auth_state: AuthState | None = None
    login_failed = False

    try:
        with conn.cursor() as cursor:

            _set_request_context(
                cursor,
                request,
                "AUTH_API",
            )


            cursor.execute(
                """
                SELECT *
                FROM app.auth_get_local_user(%s)
                """,
                (
                    username,
                ),
            )

            user = cursor.fetchone()


            encoded_hash = (
                user["password_hash"]
                if user is not None
                and user["password_hash"]
                else DUMMY_PASSWORD_HASH
            )


            password_ok = verify_password(
                payload.password,
                encoded_hash,
            )


            valid_user = (
                user is not None
                and user["is_active"] is True
                and user["password_hash"] is not None
                and password_ok
            )


            if not valid_user:

                cursor.execute(
                    """
                    SELECT app.auth_log_login_failure(
                        %s,
                        %s
                    )
                    """,
                    (
                        username,
                        "INVALID_CREDENTIALS",
                    ),
                )

                conn.commit()
                login_failed = True

            else:

                raw_token = _new_session_token()

                token_hash = _hash_session_token(
                    raw_token
                )

                expires_at = (
                    datetime.now(
                        timezone.utc
                    )
                    + timedelta(
                        hours=SESSION_TTL_HOURS
                    )
                )


                cursor.execute(
                    """
                    SELECT app.auth_create_session(
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    ) AS session_id
                    """,
                    (
                        user["user_id"],
                        token_hash,
                        expires_at,
                        request.headers.get(
                            "user-agent"
                        ),
                        _client_ip(
                            request
                        ),
                    ),
                )

                session_result = cursor.fetchone()

                session_id = session_result[
                    "session_id"
                ]


                cursor.execute(
                    """
                    SELECT *
                    FROM app.auth_get_session(%s)
                    """,
                    (
                        token_hash,
                    ),
                )

                session_row = cursor.fetchone()

                if session_row is None:
                    raise RuntimeError(
                        "Created session could not be resolved"
                    )


                cursor.execute(
                    """
                    SELECT
                        role_code,
                        role_name
                    FROM app.auth_get_roles(%s)
                    """,
                    (
                        user["user_id"],
                    ),
                )

                roles = cursor.fetchall()


                cursor.execute(
                    """
                    SELECT
                        permission_code,
                        permission_name,
                        permission_group
                    FROM app.auth_get_permissions(%s)
                    """,
                    (
                        user["user_id"],
                    ),
                )

                permissions = cursor.fetchall()


                cursor.execute(
                    """
                    SELECT
                        resource_type,
                        resource_code,
                        resource_name
                    FROM app.auth_get_resources(%s)
                    """,
                    (
                        user["user_id"],
                    ),
                )

                resources = cursor.fetchall()


                conn.commit()


                if session_row["session_id"] != session_id:
                    raise RuntimeError(
                        "Session identifier mismatch"
                    )


                auth_state = _build_auth_state(
                    session_row,
                    roles,
                    permissions,
                    resources,
                )

    finally:
        conn.close()


    if login_failed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )


    if raw_token is None or auth_state is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed",
        )


    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,

        max_age=(
            SESSION_TTL_HOURS
            * 60
            * 60
        ),

        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )

    response.headers[
        "Cache-Control"
    ] = "no-store"


    return auth_state


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
) -> ChangePasswordResponse:
    raw_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not raw_token or len(raw_token) > 512:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    state = _load_auth_state(
        request
    )

    if state is None or state.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    if state.user.auth_provider != "LOCAL":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Password change is available "
                "only for LOCAL accounts"
            ),
        )

    if (
        state.user.is_system
        or state.user.account_level == "SUPERADMIN"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "System account password change "
                "is server-side only"
            ),
        )

    config = get_api_config()

    conn = connect_control(
        config
    )

    try:
        with conn.cursor() as cursor:

            _set_request_context(
                cursor,
                request,
                "AUTH_API",
            )

            if not state.user.must_change_password:
                if not payload.current_password:
                    conn.rollback()

                    raise HTTPException(
                        status_code=(
                            status.HTTP_422_UNPROCESSABLE_ENTITY
                        ),
                        detail="Current password is required",
                    )

                cursor.execute(
                    """
                    SELECT *
                    FROM app.auth_get_local_user(%s)
                    """,
                    (
                        state.user.username,
                    ),
                )

                user = cursor.fetchone()

                if (
                    user is None
                    or user["password_hash"] is None
                    or not verify_password(
                        payload.current_password,
                        user["password_hash"],
                    )
                ):
                    conn.rollback()

                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Current password is incorrect",
                    )

            password_errors = validate_new_password(
                payload.new_password,
                state.user.username,
            )

            if password_errors:
                conn.rollback()

                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "PASSWORD_POLICY_FAILED",
                        "message": "Password does not meet policy requirements",
                        "errors": password_errors,
                    },
                )

            new_password_hash = hash_password(
                payload.new_password
            )

            cursor.execute(
                """
                SELECT app.auth_change_own_password(
                    %s,
                    %s
                ) AS changed
                """,
                (
                    _hash_session_token(
                        raw_token
                    ),
                    new_password_hash,
                ),
            )

            row = cursor.fetchone()

            if not row or not row["changed"]:
                raise RuntimeError(
                    "Password change returned no result"
                )

            conn.commit()

    finally:
        conn.close()

    refreshed = _load_auth_state_by_hash(
        _hash_session_token(
            raw_token
        )
    )

    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is no longer active",
        )

    return ChangePasswordResponse(
        changed=True,
        auth=refreshed,
    )


@router.get(
    "/me",
    response_model=AuthState,
)
def me(
    request: Request,
    response: Response,
) -> AuthState:
    response.headers[
        "Cache-Control"
    ] = "no-store"

    state = _load_auth_state(
        request
    )

    if state is None:
        return AuthState(
            authenticated=False
        )

    return state


@router.post(
    "/logout",
    response_model=LogoutResponse,
)
def logout(
    request: Request,
    response: Response,
) -> LogoutResponse:
    raw_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    logged_out = False


    if raw_token and len(raw_token) <= 512:

        config = get_api_config()

        conn = connect_control(
            config
        )

        try:
            with conn.cursor() as cursor:

                _set_request_context(
                    cursor,
                    request,
                    "AUTH_API",
                )

                cursor.execute(
                    """
                    SELECT app.auth_revoke_session(%s)
                        AS revoked
                    """,
                    (
                        _hash_session_token(
                            raw_token
                        ),
                    ),
                )

                row = cursor.fetchone()

                logged_out = bool(
                    row
                    and row["revoked"]
                )

                conn.commit()

        finally:
            conn.close()


    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )

    response.headers[
        "Cache-Control"
    ] = "no-store"


    return LogoutResponse(
        logged_out=logged_out
    )


def require_authenticated(
    request: Request,
) -> AuthState:
    state = _load_auth_state(
        request
    )

    if state is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return state


def require_admin(
    state: AuthState = Depends(
        require_authenticated
    ),
) -> AuthState:
    if (
        state.user is None
        or state.user.account_level
        not in {
            "SUPERADMIN",
            "ADMIN",
        }
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    return state


def require_superadmin(
    state: AuthState = Depends(
        require_authenticated
    ),
) -> AuthState:
    if (
        state.user is None
        or state.user.account_level
        != "SUPERADMIN"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SUPERADMIN access required",
        )

    return state


def require_permission(
    permission_code: str,
) -> Callable:
    def dependency(
        state: AuthState = Depends(
            require_authenticated
        ),
    ) -> AuthState:

        if state.user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )


        effective_codes = {
            item.permission_code
            for item in state.permissions
        }


        if permission_code not in effective_codes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Required permission: "
                    + permission_code
                ),
            )


        return state


    return dependency


def require_resource(
    resource_type: str,
    resource_code: str,
) -> Callable:
    def dependency(
        state: AuthState = Depends(
            require_authenticated
        ),
    ) -> AuthState:

        if state.user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )


        effective_resources = {
            (
                item.resource_type,
                item.resource_code,
            )
            for item in state.resources
        }


        required_resource = (
            resource_type,
            resource_code,
        )


        if required_resource not in effective_resources:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Required resource: "
                    + resource_type
                    + "/"
                    + resource_code
                ),
            )


        return state


    return dependency


def authorize_api_access(
    request: Request,
    *,
    permissions: tuple[str, ...] = (),
    resource_type: str | None = None,
    resource_code: str | None = None,
) -> AuthState:
    """
    Authorize an already-defined API resource.

    All listed permissions are required.

    If resource_type/resource_code are supplied,
    that resource must also be present in the
    user's effective resource scope.
    """

    state = _load_auth_state(
        request
    )


    if state is None or state.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )


    effective_permissions = {
        item.permission_code
        for item in state.permissions
    }


    for permission_code in permissions:

        if permission_code not in effective_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Required permission: "
                    + permission_code
                ),
            )


    if (
        resource_type is not None
        or resource_code is not None
    ):

        if (
            resource_type is None
            or resource_code is None
        ):
            raise RuntimeError(
                "Both resource_type and resource_code "
                "must be specified"
            )


        effective_resources = {
            (
                item.resource_type,
                item.resource_code,
            )
            for item in state.resources
        }


        if (
            resource_type,
            resource_code,
        ) not in effective_resources:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Required resource: "
                    + resource_type
                    + "/"
                    + resource_code
                ),
            )


    return state