from __future__ import annotations

import os
from collections import defaultdict

from psycopg import sql
from psycopg.types.json import Jsonb

from dwh.config import get_dwh_config
from dwh.db import connect


VALIDATION_ISSUE_LIMIT = int(
    os.getenv("VALIDATION_ISSUE_LIMIT", "1000")
)


def safe_applicant_expr(source_object: str) -> sql.Composable:
    if source_object == "application":
        return sql.SQL(
            """
            CASE
                WHEN pg_input_is_valid(
                    s.source_data->>'applicant_id',
                    'bigint'
                )
                THEN (s.source_data->>'applicant_id')::bigint
                ELSE NULL
            END
            """
        )

    if source_object == "applicant":
        return sql.SQL(
            """
            CASE
                WHEN pg_input_is_valid(
                    s._dwh_source_pk,
                    'bigint'
                )
                THEN s._dwh_source_pk::bigint
                ELSE NULL
            END
            """
        )

    if source_object == "integration_log":
        return sql.SQL(
            """
            CASE
                WHEN pg_input_is_valid(
                    s.source_data->>'applicant_id',
                    'bigint'
                )
                THEN (s.source_data->>'applicant_id')::bigint
                ELSE NULL
            END
            """
        )

    return sql.SQL("NULL::bigint")


def count_rows(
    conn,
    stg_table: str,
    source_batch_run_id: int,
) -> int:
    query = sql.SQL(
        """
        SELECT COUNT(*)
        FROM stg.{}
        WHERE _dwh_batch_run_id = %s
        """
    ).format(
        sql.Identifier(stg_table)
    )

    with conn.cursor() as cur:
        cur.execute(
            query,
            (source_batch_run_id,),
        )
        return int(cur.fetchone()[0])


def required_issue_count(
    conn,
    stg_table: str,
    source_column: str,
    source_batch_run_id: int,
    allow_blank: bool,
) -> int:

    condition = sql.SQL(
        """
        s.source_data->>%s IS NULL
        """
    )

    params: list[object] = [
        source_column,
    ]

    if not allow_blank:
        condition = sql.SQL(
            """
            (
                s.source_data->>%s IS NULL
                OR btrim(s.source_data->>%s) = ''
            )
            """
        )

        params = [
            source_column,
            source_column,
        ]

    query = sql.SQL(
        """
        SELECT COUNT(*)
        FROM stg.{} s
        WHERE s._dwh_batch_run_id = %s
          AND {}
        """
    ).format(
        sql.Identifier(stg_table),
        condition,
    )

    params.insert(
        0,
        source_batch_run_id,
    )

    with conn.cursor() as cur:
        cur.execute(
            query,
            tuple(params),
        )

        return int(cur.fetchone()[0])


def fetch_required_issues(
    conn,
    *,
    stg_table: str,
    source_object: str,
    source_column: str,
    source_batch_run_id: int,
    allow_blank: bool,
    limit: int,
):

    applicant_expr = safe_applicant_expr(
        source_object
    )

    condition = sql.SQL(
        """
        s.source_data->>%s IS NULL
        """
    )

    condition_params: list[object] = [
        source_column,
    ]

    if not allow_blank:
        condition = sql.SQL(
            """
            (
                s.source_data->>%s IS NULL
                OR btrim(s.source_data->>%s) = ''
            )
            """
        )

        condition_params = [
            source_column,
            source_column,
        ]

    query = sql.SQL(
        """
        SELECT
            s._dwh_landing_id,
            s._dwh_source_pk,
            s._dwh_application_id,
            {applicant_expr},

            (s.source_data ? %s) AS has_key,

            s.source_data->>%s AS raw_value

        FROM stg.{table} s

        WHERE s._dwh_batch_run_id = %s
          AND {condition}

        ORDER BY s._dwh_source_pk
        LIMIT %s
        """
    ).format(
        applicant_expr=applicant_expr,
        table=sql.Identifier(stg_table),
        condition=condition,
    )

    params: list[object] = [
        source_column,
        source_column,
        source_batch_run_id,
    ]

    params.extend(
        condition_params
    )

    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(
            query,
            tuple(params),
        )

        return cur.fetchall()


def invalid_type_count(
    conn,
    *,
    stg_table: str,
    source_column: str,
    expected_pg_type: str,
    source_batch_run_id: int,
    required: bool,
    allow_blank: bool,
) -> int:

    extra_blank_guard = sql.SQL("")

    if required and not allow_blank:
        extra_blank_guard = sql.SQL(
            """
            AND btrim(
                s.source_data->>%s
            ) <> ''
            """
        )

    query = sql.SQL(
        """
        SELECT COUNT(*)
        FROM stg.{table} s
        WHERE s._dwh_batch_run_id = %s

          AND s.source_data->>%s IS NOT NULL

          {blank_guard}

          AND NOT pg_input_is_valid(
              s.source_data->>%s,
              %s
          )
        """
    ).format(
        table=sql.Identifier(stg_table),
        blank_guard=extra_blank_guard,
    )

    params: list[object] = [
        source_batch_run_id,
        source_column,
    ]

    if required and not allow_blank:
        params.append(source_column)

    params.extend(
        [
            source_column,
            expected_pg_type,
        ]
    )

    with conn.cursor() as cur:
        cur.execute(
            query,
            tuple(params),
        )

        return int(cur.fetchone()[0])


def fetch_invalid_type_issues(
    conn,
    *,
    stg_table: str,
    source_object: str,
    source_column: str,
    expected_pg_type: str,
    source_batch_run_id: int,
    required: bool,
    allow_blank: bool,
    limit: int,
):

    applicant_expr = safe_applicant_expr(
        source_object
    )

    extra_blank_guard = sql.SQL("")

    if required and not allow_blank:
        extra_blank_guard = sql.SQL(
            """
            AND btrim(
                s.source_data->>%s
            ) <> ''
            """
        )

    query = sql.SQL(
        """
        SELECT
            s._dwh_landing_id,
            s._dwh_source_pk,
            s._dwh_application_id,
            {applicant_expr},

            s.source_data->>%s AS bad_value,

            (
                pg_input_error_info(
                    s.source_data->>%s,
                    %s
                )
            ).message AS error_message,

            (
                pg_input_error_info(
                    s.source_data->>%s,
                    %s
                )
            ).sql_error_code AS sql_state

        FROM stg.{table} s

        WHERE s._dwh_batch_run_id = %s

          AND s.source_data->>%s IS NOT NULL

          {blank_guard}

          AND NOT pg_input_is_valid(
              s.source_data->>%s,
              %s
          )

        ORDER BY s._dwh_source_pk
        LIMIT %s
        """
    ).format(
        applicant_expr=applicant_expr,
        table=sql.Identifier(stg_table),
        blank_guard=extra_blank_guard,
    )

    params: list[object] = [
        source_column,

        source_column,
        expected_pg_type,

        source_column,
        expected_pg_type,

        source_batch_run_id,

        source_column,
    ]

    if required and not allow_blank:
        params.append(source_column)

    params.extend(
        [
            source_column,
            expected_pg_type,
            limit,
        ]
    )

    with conn.cursor() as cur:
        cur.execute(
            query,
            tuple(params),
        )

        return cur.fetchall()


def insert_error(
    conn,
    *,
    batch_run_id: int,
    job_run_id: int,
    job_id: int,
    source_object_id: int,
    landing_id: int | None,
    source_pk: str | None,
    application_id: int | None,
    applicant_id: int | None,
    column_name: str,
    bad_value: str | None,
    expected_value: str,
    error_code: str,
    error_message: str,
    sql_state: str | None,
    exception_type: str,
    diagnostic_context: dict,
) -> None:

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl.error_log (
                batch_run_id,
                job_run_id,
                job_id,
                source_object_id,
                record_key,

                error_stage,
                error_code,
                error_message,

                source_pk,
                application_id,
                applicant_id,

                column_name,
                bad_value,
                expected_value,

                sql_state,
                exception_type,
                diagnostic_context
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,

                'PRE_ODS_VALIDATION',
                %s,
                %s,

                %s,
                %s,
                %s,

                %s,
                %s,
                %s,

                %s,
                %s,
                %s
            )
            """,
            (
                batch_run_id,
                job_run_id,
                job_id,
                source_object_id,
                (
                    Jsonb({
                        "dwh_landing_id": landing_id,
                    })
                    if landing_id is not None
                    else None
                ),

                error_code,
                error_message,

                source_pk,
                application_id,
                applicant_id,

                column_name,
                bad_value,
                expected_value,

                sql_state,
                exception_type,
                Jsonb(diagnostic_context),
            ),
        )


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

    with connect(config) as conn:

        # ====================================================
        # Validate destination
        # ====================================================

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
                f"Unexpected DWH user: {user}"
            )

        # ====================================================
        # Source batch
        # ====================================================

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    business_date,
                    status
                FROM etl.batch_run
                WHERE batch_run_id = %s
                """,
                (source_batch_run_id,),
            )

            source_batch = cur.fetchone()

        if source_batch is None:
            raise RuntimeError(
                f"Source batch not found: "
                f"{source_batch_run_id}"
            )

        business_date, source_status = (
            source_batch
        )

        if source_status != "SUCCESS":
            raise RuntimeError(
                "Source batch must be SUCCESS; "
                f"actual={source_status}"
            )

        # ====================================================
        # Load contracts
        # ====================================================

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    contract_id,
                    source_schema,
                    source_object,
                    stg_table,
                    source_column,
                    target_schema,
                    target_object,
                    target_column,
                    expected_pg_type,
                    is_required,
                    allow_blank
                FROM meta.v_current_column_contract
                WHERE source_system_code = 'CRM_RETAIL'
                  AND is_active = true
                ORDER BY
                    source_schema,
                    source_object,
                    contract_id
                """
            )

            contract_rows = cur.fetchall()

        if not contract_rows:
            raise RuntimeError(
                "No active column contracts"
            )

        contracts_by_object = defaultdict(list)

        for row in contract_rows:
            contracts_by_object[
                (
                    row[1],
                    row[2],
                    row[3],
                )
            ].append(row)

        # ====================================================
        # Create validation batch
        # ====================================================

        validation_code = (
            "CRM_RETAIL_VALIDATE_"
            + business_date.strftime("%Y%m%d")
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
                    message
                )
                VALUES (
                    %s,
                    %s,
                    'MANUAL',
                    'RUNNING',
                    now(),
                    'stg_validator',
                    %s
                )
                RETURNING batch_run_id
                """,
                (
                    validation_code,
                    business_date,
                    (
                        "STG technical preflight; "
                        f"source_batch_run_id="
                        f"{source_batch_run_id}"
                    ),
                ),
            )

            validation_batch_run_id = (
                cur.fetchone()[0]
            )

        conn.commit()

        # ----------------------------------------------------
        # Freeze exact technical-validation configuration.
        #
        # This batch will always remain linked to the exact
        # contract versions selected at its start.
        # ----------------------------------------------------

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE etl.batch_run
                SET
                    pipeline_code = 'STG_VALIDATION',
                    source_batch_run_id = %s
                WHERE batch_run_id = %s
                """,
                (
                    source_batch_run_id,
                    validation_batch_run_id,
                ),
            )

            cur.executemany(
                """
                INSERT INTO meta.batch_contract_snapshot (
                    batch_run_id,
                    contract_id
                )
                VALUES (
                    %s,
                    %s
                )
                """,
                [
                    (
                        validation_batch_run_id,
                        int(row[0]),
                    )
                    for row in contract_rows
                ],
            )

        conn.commit()
        print("STG VALIDATION STARTED")
        print(
            f"validation batch : "
            f"{validation_batch_run_id}"
        )
        print(
            f"source batch     : "
            f"{source_batch_run_id}"
        )
        print(
            f"business date    : "
            f"{business_date}"
        )
        print(
            f"contracts        : "
            f"{len(contract_rows)}"
        )
        print()

        total_rows = 0
        total_issues = 0
        total_logged = 0
        failed_objects = 0

        try:
            for (
                source_schema,
                source_object,
                stg_table,
            ), contracts in contracts_by_object.items():

                # --------------------------------------------
                # Resolve TRANSFORM job
                # --------------------------------------------

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            j.job_id,
                            so.source_object_id
                        FROM meta.source_object so

                        JOIN meta.source_system ss
                          ON ss.source_system_id =
                             so.source_system_id

                        JOIN etl.job j
                          ON j.source_object_id =
                             so.source_object_id

                        WHERE ss.code = 'CRM_RETAIL'
                          AND so.source_schema = %s
                          AND so.source_object = %s
                          AND j.job_type = 'TRANSFORM'
                          AND j.is_active = true
                        """,
                        (
                            source_schema,
                            source_object,
                        ),
                    )

                    job = cur.fetchone()

                if job is None:
                    raise RuntimeError(
                        "TRANSFORM job not found for "
                        f"{source_schema}."
                        f"{source_object}"
                    )

                job_id, source_object_id = job

                rows_read = count_rows(
                    conn,
                    stg_table,
                    source_batch_run_id,
                )

                # --------------------------------------------
                # Create validation job run
                # --------------------------------------------

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO etl.job_run (
                            batch_run_id,
                            job_id,
                            status,
                            started_at
                        )
                        VALUES (
                            %s,
                            %s,
                            'RUNNING',
                            now()
                        )
                        RETURNING job_run_id
                        """,
                        (
                            validation_batch_run_id,
                            job_id,
                        ),
                    )

                    job_run_id = (
                        cur.fetchone()[0]
                    )

                conn.commit()

                object_issues = 0
                object_logged = 0
                rejected_source_keys: set[str] = set()

                # --------------------------------------------
                # Validate each contract
                # --------------------------------------------

                for contract in contracts:
                    (
                        contract_id,
                        _source_schema,
                        _source_object,
                        _stg_table,
                        source_column,
                        target_schema,
                        target_object,
                        target_column,
                        expected_pg_type,
                        is_required,
                        allow_blank,
                    ) = contract

                    # ========================================
                    # REQUIRED / NULL / BLANK
                    # ========================================

                    if is_required:
                        missing_count = (
                            required_issue_count(
                                conn,
                                stg_table,
                                source_column,
                                source_batch_run_id,
                                allow_blank,
                            )
                        )

                        object_issues += (
                            missing_count
                        )

                        if missing_count > 0:
                            missing_rows = (
                                fetch_required_issues(
                                    conn,
                                    stg_table=stg_table,
                                    source_object=source_object,
                                    source_column=source_column,
                                    source_batch_run_id=(
                                        source_batch_run_id
                                    ),
                                    allow_blank=allow_blank,
                                    limit=(
                                        VALIDATION_ISSUE_LIMIT
                                    ),
                                )
                            )

                            for (
                                landing_id,
                                source_pk,
                                application_id,
                                applicant_id,
                                has_key,
                                raw_value,
                            ) in missing_rows:

                                if not has_key:
                                    bad_value = (
                                        "<MISSING>"
                                    )
                                elif raw_value is None:
                                    bad_value = (
                                        "<NULL>"
                                    )
                                elif (
                                    raw_value.strip()
                                    == ""
                                ):
                                    bad_value = (
                                        "<BLANK>"
                                    )
                                else:
                                    bad_value = (
                                        raw_value[:1000]
                                    )

                                expected = (
                                    expected_pg_type
                                    + "; required"
                                )

                                if not allow_blank:
                                    expected += (
                                        "; non-blank"
                                    )

                                insert_error(
                                    conn,
                                    batch_run_id=(
                                        validation_batch_run_id
                                    ),
                                    job_run_id=job_run_id,
                                    job_id=job_id,
                                    source_object_id=(
                                        source_object_id
                                    ),
                                    landing_id=landing_id,
                                    source_pk=source_pk,
                                    application_id=(
                                        application_id
                                    ),
                                    applicant_id=(
                                        applicant_id
                                    ),
                                    column_name=(
                                        source_column
                                    ),
                                    bad_value=bad_value,
                                    expected_value=(
                                        expected
                                    ),
                                    error_code=(
                                        "REQUIRED_VALUE_MISSING"
                                    ),
                                    error_message=(
                                        "Required source "
                                        "value is missing "
                                        "or blank"
                                    ),
                                    sql_state="23502",
                                    exception_type=(
                                        "REQUIRED_VALUE_MISSING"
                                    ),
                                    diagnostic_context={
                                        "contract_id":
                                            contract_id,
                                        "source":
                                            (
                                                f"{source_schema}."
                                                f"{source_object}"
                                            ),
                                        "stg_table":
                                            (
                                                f"stg."
                                                f"{stg_table}"
                                            ),
                                        "target":
                                            (
                                                f"{target_schema}."
                                                f"{target_object}."
                                                f"{target_column}"
                                            ),
                                        "expected_pg_type":
                                            expected_pg_type,
                                    },
                                )

                                rejected_source_keys.add(
                                    str(source_pk)
                                )

                                object_logged += 1

                    # ========================================
                    # TYPE CONVERSION
                    # ========================================

                    invalid_count = (
                        invalid_type_count(
                            conn,
                            stg_table=stg_table,
                            source_column=source_column,
                            expected_pg_type=(
                                expected_pg_type
                            ),
                            source_batch_run_id=(
                                source_batch_run_id
                            ),
                            required=is_required,
                            allow_blank=allow_blank,
                        )
                    )

                    object_issues += (
                        invalid_count
                    )

                    if invalid_count > 0:
                        invalid_rows = (
                            fetch_invalid_type_issues(
                                conn,
                                stg_table=stg_table,
                                source_object=source_object,
                                source_column=source_column,
                                expected_pg_type=(
                                    expected_pg_type
                                ),
                                source_batch_run_id=(
                                    source_batch_run_id
                                ),
                                required=is_required,
                                allow_blank=allow_blank,
                                limit=(
                                    VALIDATION_ISSUE_LIMIT
                                ),
                            )
                        )

                        for (
                            landing_id,
                            source_pk,
                            application_id,
                            applicant_id,
                            bad_value,
                            pg_message,
                            sql_state,
                        ) in invalid_rows:

                            insert_error(
                                conn,
                                batch_run_id=(
                                    validation_batch_run_id
                                ),
                                job_run_id=job_run_id,
                                job_id=job_id,
                                source_object_id=(
                                    source_object_id
                                ),
                                landing_id=landing_id,
                                source_pk=source_pk,
                                application_id=(
                                    application_id
                                ),
                                applicant_id=(
                                    applicant_id
                                ),
                                column_name=(
                                    source_column
                                ),
                                bad_value=(
                                    bad_value[:1000]
                                    if bad_value
                                    is not None
                                    else "<NULL>"
                                ),
                                expected_value=(
                                    expected_pg_type
                                ),
                                error_code=(
                                    "INVALID_CAST"
                                ),
                                error_message=(
                                    pg_message
                                    or
                                    (
                                        "Value cannot be "
                                        "converted to "
                                        f"{expected_pg_type}"
                                    )
                                ),
                                sql_state=sql_state,
                                exception_type=(
                                    "INVALID_CAST"
                                ),
                                diagnostic_context={
                                    "contract_id":
                                        contract_id,
                                    "source":
                                        (
                                            f"{source_schema}."
                                            f"{source_object}"
                                        ),
                                    "stg_table":
                                        (
                                            f"stg."
                                            f"{stg_table}"
                                        ),
                                    "target":
                                        (
                                            f"{target_schema}."
                                            f"{target_object}."
                                            f"{target_column}"
                                        ),
                                    "expected_pg_type":
                                        expected_pg_type,
                                },
                            )

                            rejected_source_keys.add(
                                str(source_pk)
                            )

                            object_logged += 1

                # --------------------------------------------
                # Finalize object job
                # --------------------------------------------

                object_status = (
                    "FAILED"
                    if object_issues > 0
                    else "SUCCESS"
                )

                if object_issues > 0:
                    failed_objects += 1

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE etl.job_run
                        SET
                            status = %s,
                            finished_at = now(),
                            rows_read = %s,
                            rows_inserted = 0,
                            rows_updated = 0,
                            rows_rejected = %s,
                            error_count = %s,
                            message = %s
                        WHERE job_run_id = %s
                        """,
                        (
                            object_status,
                            rows_read,
                            len(
                                rejected_source_keys
                            ),
                            object_issues,
                            (
                                "PRE_ODS_VALIDATION; "
                                f"issues="
                                f"{object_issues}; "
                                f"logged="
                                f"{object_logged}"
                            ),
                            job_run_id,
                        ),
                    )

                conn.commit()

                total_rows += rows_read
                total_issues += object_issues
                total_logged += object_logged

                print(
                    f"{object_status:<7} "
                    f"{source_object:<20} "
                    f"rows={rows_read:<5} "
                    f"issues={object_issues:<5} "
                    f"logged={object_logged}",
                    flush=True,
                )

            # =================================================
            # Finish validation batch
            # =================================================

            final_status = (
                "FAILED"
                if total_issues > 0
                else "SUCCESS"
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE etl.batch_run
                    SET
                        status = %s,
                        finished_at = now(),
                        message = %s
                    WHERE batch_run_id = %s
                    """,
                    (
                        final_status,
                        (
                            "STG technical preflight; "
                            f"source_batch_run_id="
                            f"{source_batch_run_id}; "
                            f"rows={total_rows}; "
                            f"issues={total_issues}; "
                            f"logged={total_logged}; "
                            f"failed_objects="
                            f"{failed_objects}"
                        ),
                        validation_batch_run_id,
                    ),
                )

            conn.commit()

            print()
            print(
                f"STG VALIDATION {final_status}"
            )
            print(
                f"validation batch : "
                f"{validation_batch_run_id}"
            )
            print(
                f"objects          : "
                f"{len(contracts_by_object)}"
            )
            print(
                f"rows             : "
                f"{total_rows}"
            )
            print(
                f"issues           : "
                f"{total_issues}"
            )
            print(
                f"issues logged    : "
                f"{total_logged}"
            )
            print(
                f"failed objects   : "
                f"{failed_objects}"
            )

            if total_issues > 0:
                raise SystemExit(2)

            return int(validation_batch_run_id)

        except BaseException as exc:
            conn.rollback()

            # A normal validation failure has already been
            # finalized above. Only convert a still-RUNNING
            # batch into FAILED here.

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status
                    FROM etl.batch_run
                    WHERE batch_run_id = %s
                    """,
                    (
                        validation_batch_run_id,
                    ),
                )

                current = cur.fetchone()

                if (
                    current is not None
                    and current[0] == "RUNNING"
                ):
                    cur.execute(
                        """
                        UPDATE etl.batch_run
                        SET
                            status = 'FAILED',
                            finished_at = now(),
                            message = %s
                        WHERE batch_run_id = %s
                        """,
                        (
                            (
                                "STG validator "
                                "execution error: "
                                f"{type(exc).__name__}: "
                                f"{str(exc)[:3000]}"
                            ),
                            validation_batch_run_id,
                        ),
                    )

            conn.commit()
            raise


if __name__ == "__main__":
    main()