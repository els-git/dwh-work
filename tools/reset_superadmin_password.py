from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


REPO_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

BACKEND_SRC = (
    REPO_ROOT
    / "backend"
    / "src"
)

BACKEND_PACKAGES = (
    REPO_ROOT
    / "backend"
    / "packages"
)

sys.path.insert(
    0,
    str(BACKEND_SRC),
)

sys.path.insert(
    0,
    str(BACKEND_PACKAGES),
)


import psycopg

from dwh_api.security_password import (
    hash_password,
    verify_password,
)


def load_env_file(
    path: Path,
) -> dict[str, str]:
    result: dict[str, str] = {}

    if not path.exists():
        return result

    for raw_line in path.read_text(
        encoding="utf-8-sig",
    ).splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1,
        )

        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        result[key] = value

    return result


def read_password() -> str:
    print()
    print(
        "SUPERADMIN password recovery"
    )
    print(
        "Password is not displayed "
        "and is not stored in command history."
    )
    print()

    while True:
        password = getpass.getpass(
            "New SUPERADMIN password: "
        )

        if len(password) < 14:
            print(
                "Password must contain "
                "at least 14 characters."
            )
            continue

        if len(password) > 256:
            print(
                "Password is too long."
            )
            continue

        confirmation = getpass.getpass(
            "Confirm password: "
        )

        if password != confirmation:
            print(
                "Passwords do not match."
            )
            continue

        return password


def main() -> None:
    env_values = load_env_file(
        REPO_ROOT / ".env"
    )

    db_password = (
        os.environ.get(
            "DWH_DB_PASSWORD"
        )
        or env_values.get(
            "DWH_DB_PASSWORD"
        )
    )

    if not db_password:
        raise RuntimeError(
            "DWH_DB_PASSWORD "
            "was not found"
        )

    password = read_password()

    encoded_hash = hash_password(
        password
    )

    if not verify_password(
        password,
        encoded_hash,
    ):
        raise RuntimeError(
            "Password hash "
            "self-check failed"
        )

    connection = psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="dwh",
        user="dwh_admin",
        password=db_password,
    )

    try:
        with connection:
            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT
                        user_id,
                        username,
                        account_level,
                        auth_provider,
                        is_active,
                        is_system,
                        password_hash IS NOT NULL
                            AS password_initialized
                    FROM app.user_account
                    WHERE username = 'superadmin'
                    FOR UPDATE
                    """
                )

                row = cursor.fetchone()

                if row is None:
                    raise RuntimeError(
                        "SUPERADMIN account "
                        "does not exist"
                    )

                (
                    user_id,
                    username,
                    account_level,
                    auth_provider,
                    is_active,
                    is_system,
                    password_initialized,
                ) = row

                if account_level != "SUPERADMIN":
                    raise RuntimeError(
                        "Invalid SUPERADMIN "
                        "account level"
                    )

                if auth_provider != "LOCAL":
                    raise RuntimeError(
                        "SUPERADMIN must use "
                        "LOCAL authentication"
                    )

                if not is_active:
                    raise RuntimeError(
                        "SUPERADMIN account "
                        "must be active"
                    )

                if not is_system:
                    raise RuntimeError(
                        "SUPERADMIN account "
                        "must be system account"
                    )

                cursor.execute(
                    """
                    SELECT set_config(
                        'dwh.superadmin_recovery',
                        'on',
                        true
                    )
                    """
                )

                cursor.execute(
                    """
                    UPDATE app.user_account
                    SET
                        password_hash = %s,
                        updated_at = now()
                    WHERE user_id = %s
                    """,
                    (
                        encoded_hash,
                        user_id,
                    ),
                )

                event_type = (
                    "SUPERADMIN_PASSWORD_RESET"
                    if password_initialized
                    else
                    "SUPERADMIN_PASSWORD_INITIALIZED"
                )

                cursor.execute(
                    """
                    INSERT INTO audit.security_event (
                        actor_user_id,
                        actor_username,
                        actor_account_level,
                        event_type,
                        entity_type,
                        entity_key,
                        action,
                        outcome,
                        source,
                        details
                    )
                    VALUES (
                        %s,
                        %s,
                        'SUPERADMIN',
                        %s,
                        'USER',
                        %s,
                        'SET_PASSWORD',
                        'SUCCESS',
                        'SERVER_CLI',
                        jsonb_build_object(
                            'recovery_mode',
                            true
                        )
                    )
                    """,
                    (
                        user_id,
                        username,
                        event_type,
                        username,
                    ),
                )

        print()
        print(
            "SUPERADMIN password updated."
        )
        print(
            "Account: superadmin"
        )
        print(
            f"Event: {event_type}"
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()