"""Dispatcher of the DWH server (one pass per call): due object schedules become requests (I09, package 032), then
an object run request (I08, package 015), otherwise a DM mart run request (I19, package 018). The old CRM queue etl.run_request and its tasks (VALIDATE_STG, TRANSFORM_ODS,
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


_SCHEDULE_WARNED = False


def fire_schedules(
    conn,
    name: str,
) -> int:
    """I09 (package 032): due schedules and retries of failed scheduled runs become object run requests.

    Called on every cycle before the object queue. Without package 032 in the database nothing happens. A failure
    is reported once (until the next success) and never stops the dispatcher.
    """
    global _SCHEDULE_WARNED

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regprocedure('etl.object_schedule_fire(text)') IS NOT NULL")
            present = cur.fetchone()[0]
            created = 0
            if present:
                cur.execute("SELECT etl.object_schedule_fire(%s)", (name,))
                created = int(cur.fetchone()[0] or 0)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - schedules must not stop manual runs
        conn.rollback()
        if not _SCHEDULE_WARNED:
            print("WARNING: schedules were not processed:", f"{type(exc).__name__}: {exc}")
            _SCHEDULE_WARNED = True
        return 0
    _SCHEDULE_WARNED = False
    if created:
        print(f"SCHEDULED REQUESTS CREATED: {created}")
    return created


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

        # I09: due schedules become requests of the object queue first.
        fire_schedules(
            conn,
            name,
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
