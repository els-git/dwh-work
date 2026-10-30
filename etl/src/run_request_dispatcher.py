from __future__ import annotations

import importlib
import os
import socket
from dataclasses import dataclass

from dwh.config import get_dwh_config
from dwh.db import connect


ALLOWED_TASKS = {
    "VALIDATE_STG": {
        "module": "validate_stg",
        "pipeline": "STG_VALIDATION",
        "result_kind": "batch",
    },
    "TRANSFORM_ODS": {
        "module": "transform_ods",
        "pipeline": "ODS_TRANSFORM",
        "result_kind": "batch",
    },
    "REFRESH_MARTS": {
        "module": "refresh_marts",
        "pipeline": "DM_REFRESH",
        "result_kind": "batch",
    },
    "RUN_DQ": {
        "module": "run_dq",
        "pipeline": None,
        "result_kind": "dq",
    },
}


@dataclass(frozen=True)
class RunRequest:
    run_request_id: int
    task_code: str
    source_batch_run_id: int
    load_mode: str | None


def worker_name() -> str:
    host = socket.gethostname().strip() or "unknown-host"
    return f"integration-dispatcher@{host}"[:255]


def claim_request(
    conn,
    name: str,
) -> RunRequest | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                run_request_id,
                task_code,
                source_batch_run_id,
                load_mode
            FROM etl.integration_run_request_claim(%s)
            """,
            (name,),
        )

        row = cur.fetchone()

    conn.commit()

    if row is None:
        return None

    return RunRequest(
        run_request_id=int(row[0]),
        task_code=str(row[1]),
        source_batch_run_id=int(row[2]),
        load_mode=(
            None
            if row[3] is None
            else str(row[3])
        ),
    )


def mark_started(
    conn,
    request: RunRequest,
    name: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT etl.integration_run_request_start(
                %s,
                %s
            )
            """,
            (
                request.run_request_id,
                name,
            ),
        )

        row = cur.fetchone()

        if (
            row is None
            or row[0] is not True
        ):
            raise RuntimeError(
                "Run request start was not confirmed"
            )

    conn.commit()


def mark_failed(
    conn,
    request_id: int,
    message: str,
) -> None:
    error_text = (
        message.strip()
        or "Dispatcher task failed"
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT etl.integration_run_request_fail(
                %s,
                %s
            )
            """,
            (
                request_id,
                error_text[:4000],
            ),
        )

    conn.commit()


def current_max_batch_id(
    conn,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT coalesce(
                max(batch_run_id),
                0
            )
            FROM etl.batch_run
            """
        )

        return int(cur.fetchone()[0])


def current_max_dq_id(
    conn,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT coalesce(
                max(dq_run_id),
                0
            )
            FROM dq.run
            """
        )

        return int(cur.fetchone()[0])


def resolve_batch_result(
    conn,
    *,
    after_id: int,
    source_batch_run_id: int,
    pipeline_code: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT batch_run_id
            FROM etl.batch_run
            WHERE batch_run_id > %s
              AND pipeline_code = %s
              AND source_batch_run_id = %s
              AND status = 'SUCCESS'
            ORDER BY batch_run_id DESC
            LIMIT 1
            """,
            (
                after_id,
                pipeline_code,
                source_batch_run_id,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "Successful ETL result batch was not found"
        )

    return int(row[0])


def resolve_dq_result(
    conn,
    *,
    after_id: int,
    source_batch_run_id: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT dq_run_id
            FROM dq.run
            WHERE dq_run_id > %s
              AND source_batch_run_id = %s
              AND status = 'SUCCESS'
            ORDER BY dq_run_id DESC
            LIMIT 1
            """,
            (
                after_id,
                source_batch_run_id,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "Successful DQ result was not found"
        )

    return int(row[0])


def execute_task(
    conn,
    request: RunRequest,
) -> tuple[int | None, int | None]:
    spec = ALLOWED_TASKS.get(
        request.task_code
    )

    if spec is None:
        raise RuntimeError(
            "Unsupported dispatcher task"
        )

    os.environ[
        "SOURCE_BATCH_RUN_ID"
    ] = str(
        request.source_batch_run_id
    )

    if request.task_code == "TRANSFORM_ODS":
        if request.load_mode not in {
            "FULL",
            "CONTINUE",
        }:
            raise RuntimeError(
                "Invalid TRANSFORM_ODS load mode"
            )

        os.environ[
            "LOAD_MODE"
        ] = request.load_mode
    else:
        os.environ.pop(
            "LOAD_MODE",
            None,
        )

    module = importlib.import_module(
        str(spec["module"])
    )

    task_main = getattr(
        module,
        "main",
        None,
    )

    if not callable(task_main):
        raise RuntimeError(
            "ETL module has no callable main()"
        )

    if spec["result_kind"] == "batch":
        before_id = current_max_batch_id(
            conn
        )

        returned = task_main()

        if (
            isinstance(returned, int)
            and returned > 0
        ):
            result_batch_id = returned
        else:
            result_batch_id = (
                resolve_batch_result(
                    conn,
                    after_id=before_id,
                    source_batch_run_id=(
                        request.source_batch_run_id
                    ),
                    pipeline_code=str(
                        spec["pipeline"]
                    ),
                )
            )

        return (
            result_batch_id,
            None,
        )

    before_id = current_max_dq_id(
        conn
    )

    returned = task_main()

    if (
        isinstance(returned, int)
        and returned > 0
    ):
        result_dq_id = returned
    else:
        result_dq_id = (
            resolve_dq_result(
                conn,
                after_id=before_id,
                source_batch_run_id=(
                    request.source_batch_run_id
                ),
            )
        )

    return (
        None,
        result_dq_id,
    )


def mark_success(
    conn,
    request: RunRequest,
    *,
    result_batch_run_id: int | None,
    result_dq_run_id: int | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT etl.integration_run_request_success(
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                request.run_request_id,
                result_batch_run_id,
                result_dq_run_id,
                (
                    "Completed by "
                    "integration dispatcher"
                ),
            ),
        )

        row = cur.fetchone()

        if (
            row is None
            or row[0] is not True
        ):
            raise RuntimeError(
                "Run request success was not confirmed"
            )

    conn.commit()


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

        request = claim_request(
            conn,
            name,
        )

        if request is None:
            if not quiet_empty:
                print(
                    "NO QUEUED REQUEST"
                )
            return

        print(
            "CLAIMED",
            request.run_request_id,
            request.task_code,
            request.source_batch_run_id,
            request.load_mode,
        )

        try:
            mark_started(
                conn,
                request,
                name,
            )

            print(
                "RUNNING",
                request.run_request_id,
            )

            (
                result_batch_run_id,
                result_dq_run_id,
            ) = execute_task(
                conn,
                request,
            )

            mark_success(
                conn,
                request,
                result_batch_run_id=(
                    result_batch_run_id
                ),
                result_dq_run_id=(
                    result_dq_run_id
                ),
            )

            print(
                "SUCCESS",
                request.run_request_id,
                "batch=",
                result_batch_run_id,
                "dq=",
                result_dq_run_id,
            )

        except BaseException as exc:
            try:
                conn.rollback()

                mark_failed(
                    conn,
                    request.run_request_id,
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                )
            except Exception as fail_exc:
                print(
                    "WARNING: could not mark "
                    "request FAILED:",
                    fail_exc,
                )

            raise

        finally:
            os.environ.pop(
                "SOURCE_BATCH_RUN_ID",
                None,
            )
            os.environ.pop(
                "LOAD_MODE",
                None,
            )


if __name__ == "__main__":
    main()