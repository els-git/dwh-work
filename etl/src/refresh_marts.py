from __future__ import annotations

import os
from datetime import datetime, timezone

from dwh.config import get_dwh_config
from dwh.db import connect


PIPELINE_CODE = "DM_REFRESH"

JOB_CODE = (
    "REFRESH_DM_RETAIL_APPLICATION"
)


def load_source_ods_batch(
    conn,
    source_batch_run_id: int,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                batch_run_id,
                business_date,
                pipeline_code,
                status
            FROM etl.batch_run
            WHERE batch_run_id = %s
            """,
            (
                source_batch_run_id,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "Source ODS batch does not exist: "
            f"{source_batch_run_id}"
        )

    (
        batch_run_id,
        business_date,
        pipeline_code,
        status,
    ) = row

    if pipeline_code != "ODS_TRANSFORM":
        raise RuntimeError(
            "Source batch is not ODS_TRANSFORM: "
            f"batch={batch_run_id}; "
            f"pipeline={pipeline_code}"
        )

    if status != "SUCCESS":
        raise RuntimeError(
            "Source ODS batch is not SUCCESS: "
            f"batch={batch_run_id}; "
            f"status={status}"
        )

    if business_date is None:
        raise RuntimeError(
            "Source ODS batch has no business_date: "
            f"{batch_run_id}"
        )

    return {
        "batch_run_id": int(
            batch_run_id
        ),
        "business_date":
            business_date,
    }


def resolve_mart_job(
    conn,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT job_id
            FROM etl.job
            WHERE code = %s
              AND job_type = 'MART'
              AND is_active = true
            """,
            (
                JOB_CODE,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "Active MART job not found: "
            + JOB_CODE
        )

    return int(
        row[0]
    )


def create_batch_run(
    conn,
    *,
    source_batch_run_id: int,
    business_date,
) -> int:
    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    batch_code = (
        "CRM_RETAIL_DM_"
        + business_date.strftime(
            "%Y%m%d"
        )
        + "_SRC"
        + str(
            source_batch_run_id
        )
        + "_"
        + stamp
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl.batch_run (
                batch_code,
                business_date,

                trigger_type,
                status,

                started_at,

                created_by,
                message,

                pipeline_code,
                source_batch_run_id,

                run_mode,

                root_batch_run_id,
                resume_from_batch_run_id,

                attempt_no,
                preflight_batch_run_id
            )
            VALUES (
                %s,
                %s,

                'MANUAL',
                'RUNNING',

                now(),

                'dm_orchestrator',
                %s,

                %s,
                %s,

                'FULL',

                NULL,
                NULL,

                1,
                NULL
            )
            RETURNING batch_run_id
            """,
            (
                batch_code,
                business_date,
                (
                    "DM FULL execution; "
                    "source_ods_batch_run_id="
                    + str(
                        source_batch_run_id
                    )
                ),
                PIPELINE_CODE,
                source_batch_run_id,
            ),
        )

        batch_run_id = int(
            cur.fetchone()[0]
        )

        # The root of an independent FULL
        # execution is the batch itself.
        cur.execute(
            """
            UPDATE etl.batch_run
            SET root_batch_run_id =
                batch_run_id
            WHERE batch_run_id = %s
            """,
            (
                batch_run_id,
            ),
        )

    conn.commit()

    return batch_run_id


def create_job_run(
    conn,
    *,
    batch_run_id: int,
    job_id: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl.job_run (
                batch_run_id,
                job_id,

                status,
                started_at,

                message
            )
            VALUES (
                %s,
                %s,

                'RUNNING',
                now(),

                'Refreshing dm.retail_application_history'
            )
            RETURNING job_run_id
            """,
            (
                batch_run_id,
                job_id,
            ),
        )

        job_run_id = int(
            cur.fetchone()[0]
        )

    conn.commit()

    return job_run_id


def execute_refresh(
    conn,
    *,
    batch_run_id: int,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                closed_rows,
                inserted_rows,
                unchanged_rows
            FROM dm.refresh_retail_application(
                %s
            )
            """,
            (
                batch_run_id,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "Data Mart refresh returned no result"
        )

    closed_rows = int(
        row[0]
    )

    inserted_rows = int(
        row[1]
    )

    unchanged_rows = int(
        row[2]
    )

    rows_read = (
        inserted_rows
        + unchanged_rows
    )

    conn.commit()

    return {
        "rows_read":
            rows_read,

        "closed_rows":
            closed_rows,

        "inserted_rows":
            inserted_rows,

        "unchanged_rows":
            unchanged_rows,
    }


def close_success(
    conn,
    *,
    batch_run_id: int,
    job_run_id: int,
    result,
) -> None:
    message = (
        "FULL; "
        f"read={result['rows_read']}; "
        f"inserted={result['inserted_rows']}; "
        f"closed={result['closed_rows']}; "
        f"unchanged={result['unchanged_rows']}"
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE etl.job_run
            SET
                status = 'SUCCESS',
                finished_at = now(),

                rows_read = %s,
                rows_inserted = %s,

                -- Closing an SCD2 version is
                -- represented as an update metric.
                rows_updated = %s,

                rows_rejected = 0,
                error_count = 0,

                message = %s

            WHERE job_run_id = %s
              AND status = 'RUNNING'
            """,
            (
                result[
                    "rows_read"
                ],
                result[
                    "inserted_rows"
                ],
                result[
                    "closed_rows"
                ],
                message,
                job_run_id,
            ),
        )

        cur.execute(
            """
            UPDATE etl.batch_run
            SET
                status = 'SUCCESS',
                finished_at = now(),

                message =
                    COALESCE(
                        message,
                        ''
                    )
                    || %s

            WHERE batch_run_id = %s
              AND status = 'RUNNING'
            """,
            (
                (
                    "; SUCCESS; "
                    + message
                ),
                batch_run_id,
            ),
        )

    conn.commit()


def close_failed(
    conn,
    *,
    batch_run_id: int | None,
    job_run_id: int | None,
    error_text: str,
) -> None:
    conn.rollback()

    with conn.cursor() as cur:
        if job_run_id is not None:
            cur.execute(
                """
                UPDATE etl.job_run
                SET
                    status = 'FAILED',
                    finished_at = now(),
                    error_count = 1,

                    message =
                        COALESCE(
                            message,
                            ''
                        )
                        || %s

                WHERE job_run_id = %s
                  AND status = 'RUNNING'
                """,
                (
                    (
                        "; FAILED: "
                        + error_text[
                            :3000
                        ]
                    ),
                    job_run_id,
                ),
            )

        if batch_run_id is not None:
            cur.execute(
                """
                UPDATE etl.batch_run
                SET
                    status = 'FAILED',
                    finished_at = now(),

                    message =
                        COALESCE(
                            message,
                            ''
                        )
                        || %s

                WHERE batch_run_id = %s
                  AND status = 'RUNNING'
                """,
                (
                    (
                        "; FAILED: "
                        + error_text[
                            :3000
                        ]
                    ),
                    batch_run_id,
                ),
            )

    conn.commit()


def main() -> None:
    source_batch_run_id = int(
        os.getenv(
            "SOURCE_BATCH_RUN_ID",
            "0",
        )
    )

    if source_batch_run_id <= 0:
        raise RuntimeError(
            "SOURCE_BATCH_RUN_ID is not set"
        )

    config = get_dwh_config()

    batch_run_id: int | None = None
    job_run_id: int | None = None

    with connect(
        config
    ) as conn:
        source_batch = (
            load_source_ods_batch(
                conn,
                source_batch_run_id,
            )
        )

        job_id = resolve_mart_job(
            conn
        )

        batch_run_id = (
            create_batch_run(
                conn,
                source_batch_run_id=(
                    source_batch_run_id
                ),
                business_date=(
                    source_batch[
                        "business_date"
                    ]
                ),
            )
        )

        job_run_id = (
            create_job_run(
                conn,
                batch_run_id=(
                    batch_run_id
                ),
                job_id=job_id,
            )
        )

        try:
            result = execute_refresh(
                conn,
                batch_run_id=(
                    batch_run_id
                ),
            )

            close_success(
                conn,
                batch_run_id=(
                    batch_run_id
                ),
                job_run_id=(
                    job_run_id
                ),
                result=result,
            )

        except Exception as exc:
            error_text = (
                type(exc).__name__
                + ": "
                + str(exc)
            )

            close_failed(
                conn,
                batch_run_id=(
                    batch_run_id
                ),
                job_run_id=(
                    job_run_id
                ),
                error_text=(
                    error_text
                ),
            )

            raise

    print()
    print("=" * 60)
    print("DM REFRESH SUCCESS")
    print(
        "batch_run_id :",
        batch_run_id,
    )
    print(
        "source ODS   :",
        source_batch_run_id,
    )
    print(
        "rows read    :",
        result[
            "rows_read"
        ],
    )
    print(
        "inserted     :",
        result[
            "inserted_rows"
        ],
    )
    print(
        "closed       :",
        result[
            "closed_rows"
        ],
    )
    print(
        "unchanged    :",
        result[
            "unchanged_rows"
        ],
    )


if __name__ == "__main__":
    main()