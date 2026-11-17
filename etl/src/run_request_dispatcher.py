"""Dispatcher of the DWH server (one pass per call): an object run request (I08, package 015), otherwise a DM mart
run request (I19, package 018). The old CRM queue etl.run_request and its tasks (VALIDATE_STG, TRANSFORM_ODS,
REFRESH_MARTS, RUN_DQ) were removed in I18 (package 020): every source is loaded as objects.
"""
from __future__ import annotations

import socket

from dwh.config import get_dwh_config
from dwh.db import connect


def worker_name() -> str:
    host = socket.gethostname().strip() or "unknown-host"
    return f"integration-dispatcher@{host}"[:255]


_OBJECT_WORKER_WARNED = False


def process_object_request(
    conn,
    name: str,
) -> bool:
    """One object run request (I08, package 015); False when none is queued.

    The object engine needs backend/src on the path (source gateway, SecretStore). Without it the
    object queue is skipped with one warning.
    """
    global _OBJECT_WORKER_WARNED

    try:
        from object_request_worker import (
            process_one,
        )
    except ImportError as exc:
        if not _OBJECT_WORKER_WARNED:
            print(
                "WARNING: object run requests are not processed:",
                f"{type(exc).__name__}: {exc}",
            )
            _OBJECT_WORKER_WARNED = True
        return False

    return process_one(
        conn,
        name,
    )


_MART_WORKER_WARNED = False


def process_mart_request(
    conn,
    name: str,
) -> bool:
    """One DM mart run request (I19, package 018); False when none can be claimed.

    Taken when the object queue is empty. Without backend/src on the path the mart queue is
    skipped with one warning.
    """
    global _MART_WORKER_WARNED

    try:
        from dm_mart_worker import (
            process_one,
        )
    except ImportError as exc:
        if not _MART_WORKER_WARNED:
            print(
                "WARNING: DM mart run requests are not processed:",
                f"{type(exc).__name__}: {exc}",
            )
            _MART_WORKER_WARNED = True
        return False

    return process_one(
        conn,
        name,
    )


def main(
    *,
    quiet_empty: bool = False,
) -> None:
    config = get_dwh_config()
    name = worker_name()

    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_database(),
                    current_user
                """
            )

            database, user = cur.fetchone()

        if database != "dwh":
            raise RuntimeError(
                f"Unexpected database: {database}"
            )

        if user != "dwh_etl":
            raise RuntimeError(
                "Dispatcher must run as dwh_etl"
            )

        if process_object_request(
            conn,
            name,
        ):
            return

        # I19: DM marts are reloaded when the object queue is empty.
        if process_mart_request(
            conn,
            name,
        ):
            return

        if not quiet_empty:
            print(
                "NO QUEUED REQUEST"
            )


if __name__ == "__main__":
    main()
