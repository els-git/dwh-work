from __future__ import annotations

import os

from dwh.config import get_dwh_config
from dwh.db import connect


# Every issue query MUST return exactly these columns:
#
# source_schema
# source_object
# source_pk
# application_id
# applicant_id
# column_name
# bad_value
# expected_value
# error_code
# error_message


RULES = {

    # ========================================================
    # RECONCILIATION: STG row must exist in ODS
    # ========================================================

    "CRM_RETAIL_APPLICATION_COUNT": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_application
            WHERE _dwh_batch_run_id = %s
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'application'::text,
                s._dwh_source_pk::text,
                (s.source_data->>'id')::bigint,
                (s.source_data->>'applicant_id')::bigint,
                NULL::text,
                s._dwh_source_pk::text,
                'row present in ods.crm_retail_application'::text,
                'ODS_ROW_MISSING'::text,
                'STG application row is missing from ODS'::text
            FROM stg.crm_retail_application s
            LEFT JOIN ods.crm_retail_application o
              ON o.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND o.source_id IS NULL
        """,
    },

    "CRM_RETAIL_APPLICANT_COUNT": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_applicant
            WHERE _dwh_batch_run_id = %s
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'applicant'::text,
                s._dwh_source_pk::text,
                s._dwh_application_id,
                (s._dwh_source_pk)::bigint,
                NULL::text,
                s._dwh_source_pk::text,
                'row present in ods.crm_retail_applicant'::text,
                'ODS_ROW_MISSING'::text,
                'STG applicant row is missing from ODS'::text
            FROM stg.crm_retail_applicant s
            LEFT JOIN ods.crm_retail_applicant o
              ON o.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND o.source_id IS NULL
        """,
    },

    "CRM_RETAIL_CREDIT_INFO_COUNT": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_credit_info
            WHERE _dwh_batch_run_id = %s
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'credit_info'::text,
                s._dwh_source_pk::text,
                s._dwh_application_id,
                NULL::bigint,
                NULL::text,
                s._dwh_source_pk::text,
                'row present in ods.crm_retail_credit_info'::text,
                'ODS_ROW_MISSING'::text,
                'STG credit_info row is missing from ODS'::text
            FROM stg.crm_retail_credit_info s
            LEFT JOIN ods.crm_retail_credit_info o
              ON o.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND o.source_id IS NULL
        """,
    },

    "CRM_RETAIL_WORKFLOW_COUNT": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_workflow_history
            WHERE _dwh_batch_run_id = %s
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'workflow_history'::text,
                s._dwh_source_pk::text,
                s._dwh_application_id,
                NULL::bigint,
                NULL::text,
                s._dwh_source_pk::text,
                'row present in ods.crm_retail_workflow_history'::text,
                'ODS_ROW_MISSING'::text,
                'STG workflow row is missing from ODS'::text
            FROM stg.crm_retail_workflow_history s
            LEFT JOIN ods.crm_retail_workflow_history o
              ON o.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND o.source_id IS NULL
        """,
    },

    "CRM_RETAIL_INTEGRATION_COUNT": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_integration_log
            WHERE _dwh_batch_run_id = %s
        """,
        "issue_sql": """
            SELECT
                'integration_retail'::text,
                'integration_log'::text,
                s._dwh_source_pk::text,
                (s.source_data->>'application_id')::bigint,
                (s.source_data->>'applicant_id')::bigint,
                NULL::text,
                s._dwh_source_pk::text,
                'row present in ods.crm_retail_integration_event'::text,
                'ODS_ROW_MISSING'::text,
                'STG integration event is missing from ODS'::text
            FROM stg.crm_retail_integration_log s
            LEFT JOIN ods.crm_retail_integration_event o
              ON o.source_event_hash = s._dwh_source_pk
            WHERE s._dwh_batch_run_id = %s
              AND o.source_event_hash IS NULL
        """,
    },


    # ========================================================
    # APPLICATION -> APPLICANT
    # ========================================================

    "CRM_RETAIL_APPLICATION_APPLICANT": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_application s
            JOIN ods.crm_retail_application a
              ON a.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND a.applicant_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'application'::text,
                a.source_id::text,
                a.source_id,
                a.applicant_id,
                'applicant_id'::text,
                a.applicant_id::text,
                'existing ods.crm_retail_applicant.source_id'::text,
                'APPLICANT_NOT_FOUND'::text,
                'Application references an applicant that is missing from ODS'::text
            FROM stg.crm_retail_application s
            JOIN ods.crm_retail_application a
              ON a.source_id = (s._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_applicant p
              ON p.source_id = a.applicant_id
            WHERE s._dwh_batch_run_id = %s
              AND a.applicant_id IS NOT NULL
              AND p.source_id IS NULL
        """,
    },


    # ========================================================
    # APPLICANT -> APPLICATION
    # ========================================================

    "CRM_RETAIL_APPLICANT_APPLICATION": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_applicant s
            JOIN ods.crm_retail_applicant p
              ON p.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND p.application_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'applicant'::text,
                p.source_id::text,
                p.application_id,
                p.source_id,
                'application_id'::text,
                p.application_id::text,
                'existing ods.crm_retail_application.source_id'::text,
                'APPLICATION_NOT_FOUND'::text,
                'Applicant references an application that is missing from ODS'::text
            FROM stg.crm_retail_applicant s
            JOIN ods.crm_retail_applicant p
              ON p.source_id = (s._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_application a
              ON a.source_id = p.application_id
            WHERE s._dwh_batch_run_id = %s
              AND p.application_id IS NOT NULL
              AND a.source_id IS NULL
        """,
    },


    # ========================================================
    # CREDIT INFO -> APPLICATION
    # ========================================================

    "CRM_RETAIL_CREDIT_APPLICATION": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_credit_info s
            JOIN ods.crm_retail_credit_info c
              ON c.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
              AND c.application_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'credit_info'::text,
                c.source_id::text,
                c.application_id,
                NULL::bigint,
                'application_id'::text,
                c.application_id::text,
                'existing ods.crm_retail_application.source_id'::text,
                'APPLICATION_NOT_FOUND'::text,
                'Credit info references an application that is missing from ODS'::text
            FROM stg.crm_retail_credit_info s
            JOIN ods.crm_retail_credit_info c
              ON c.source_id = (s._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_application a
              ON a.source_id = c.application_id
            WHERE s._dwh_batch_run_id = %s
              AND c.application_id IS NOT NULL
              AND a.source_id IS NULL
        """,
    },


    # ========================================================
    # WORKFLOW -> APPLICATION
    # ========================================================

    "CRM_RETAIL_WORKFLOW_APPLICATION": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_workflow_history s
            JOIN ods.crm_retail_workflow_history w
              ON w.source_id = (s._dwh_source_pk)::bigint
            WHERE s._dwh_batch_run_id = %s
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'workflow_history'::text,
                w.source_id::text,
                w.application_id,
                NULL::bigint,
                'application_id'::text,
                w.application_id::text,
                'existing ods.crm_retail_application.source_id'::text,
                'APPLICATION_NOT_FOUND'::text,
                'Workflow row references an application that is missing from ODS'::text
            FROM stg.crm_retail_workflow_history s
            JOIN ods.crm_retail_workflow_history w
              ON w.source_id = (s._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_application a
              ON a.source_id = w.application_id
            WHERE s._dwh_batch_run_id = %s
              AND a.source_id IS NULL
        """,
    },


    # ========================================================
    # APPLICATION -> STAGE
    # ========================================================

    "CRM_RETAIL_APPLICATION_STAGE": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_application x
            JOIN ods.crm_retail_application a
              ON a.source_id = (x._dwh_source_pk)::bigint
            WHERE x._dwh_batch_run_id = %s
              AND a.stage_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'application'::text,
                a.source_id::text,
                a.source_id,
                a.applicant_id,
                'stage_id'::text,
                a.stage_id::text,
                'existing ods.crm_retail_stage.source_id'::text,
                'STAGE_NOT_FOUND'::text,
                'Application stage is missing from the stage dictionary'::text
            FROM stg.crm_retail_application x
            JOIN ods.crm_retail_application a
              ON a.source_id = (x._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_stage d
              ON d.source_id = a.stage_id
            WHERE x._dwh_batch_run_id = %s
              AND a.stage_id IS NOT NULL
              AND d.source_id IS NULL
        """,
    },


    # ========================================================
    # APPLICATION -> STATUS
    # ========================================================

    "CRM_RETAIL_APPLICATION_STATUS": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_application x
            JOIN ods.crm_retail_application a
              ON a.source_id = (x._dwh_source_pk)::bigint
            WHERE x._dwh_batch_run_id = %s
              AND a.status_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'application'::text,
                a.source_id::text,
                a.source_id,
                a.applicant_id,
                'status_id'::text,
                a.status_id::text,
                'existing ods.crm_retail_status.source_id'::text,
                'STATUS_NOT_FOUND'::text,
                'Application status is missing from the status dictionary'::text
            FROM stg.crm_retail_application x
            JOIN ods.crm_retail_application a
              ON a.source_id = (x._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_status d
              ON d.source_id = a.status_id
            WHERE x._dwh_batch_run_id = %s
              AND a.status_id IS NOT NULL
              AND d.source_id IS NULL
        """,
    },


    # ========================================================
    # CREDIT INFO -> CURRENCY
    # ========================================================

    "CRM_RETAIL_CREDIT_CURRENCY": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_credit_info x
            JOIN ods.crm_retail_credit_info c
              ON c.source_id = (x._dwh_source_pk)::bigint
            WHERE x._dwh_batch_run_id = %s
              AND c.currency_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'app_retail'::text,
                'credit_info'::text,
                c.source_id::text,
                c.application_id,
                NULL::bigint,
                'currency_id'::text,
                c.currency_id::text,
                'existing ods.crm_retail_currency.source_id'::text,
                'CURRENCY_NOT_FOUND'::text,
                'Credit currency is missing from the currency dictionary'::text
            FROM stg.crm_retail_credit_info x
            JOIN ods.crm_retail_credit_info c
              ON c.source_id = (x._dwh_source_pk)::bigint
            LEFT JOIN ods.crm_retail_currency d
              ON d.source_id = c.currency_id
            WHERE x._dwh_batch_run_id = %s
              AND c.currency_id IS NOT NULL
              AND d.source_id IS NULL
        """,
    },


    # ========================================================
    # INTEGRATION -> APPLICATION
    # ========================================================

    "CRM_RETAIL_INTEGRATION_APPLICATION": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_integration_log x
            JOIN ods.crm_retail_integration_event i
              ON i.source_event_hash = x._dwh_source_pk
            WHERE x._dwh_batch_run_id = %s
              AND i.application_id IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'integration_retail'::text,
                'integration_log'::text,
                i.source_event_hash::text,
                i.application_id,
                i.applicant_id,
                'application_id'::text,
                i.application_id::text,
                'existing ods.crm_retail_application.source_id'::text,
                'APPLICATION_NOT_FOUND'::text,
                'Integration event references an application that is missing from ODS'::text
            FROM stg.crm_retail_integration_log x
            JOIN ods.crm_retail_integration_event i
              ON i.source_event_hash = x._dwh_source_pk
            LEFT JOIN ods.crm_retail_application a
              ON a.source_id = i.application_id
            WHERE x._dwh_batch_run_id = %s
              AND i.application_id IS NOT NULL
              AND a.source_id IS NULL
        """,
    },


    # ========================================================
    # INTEGRATION RESPONSE TIME
    # ========================================================

    "CRM_RETAIL_RESPONSE_TIME": {
        "checked_sql": """
            SELECT COUNT(*)
            FROM stg.crm_retail_integration_log x
            JOIN ods.crm_retail_integration_event i
              ON i.source_event_hash = x._dwh_source_pk
            WHERE x._dwh_batch_run_id = %s
              AND i.rq_date IS NOT NULL
              AND i.rs_date IS NOT NULL
        """,
        "issue_sql": """
            SELECT
                'integration_retail'::text,
                'integration_log'::text,
                i.source_event_hash::text,
                i.application_id,
                i.applicant_id,
                'response_ms'::text,
                COALESCE(i.response_ms::text, 'NULL'),
                'response_ms >= 0'::text,
                'INVALID_RESPONSE_TIME'::text,
                'Integration response time is NULL or negative although request and response timestamps are present'::text
            FROM stg.crm_retail_integration_log x
            JOIN ods.crm_retail_integration_event i
              ON i.source_event_hash = x._dwh_source_pk
            WHERE x._dwh_batch_run_id = %s
              AND i.rq_date IS NOT NULL
              AND i.rs_date IS NOT NULL
              AND (
                    i.response_ms IS NULL
                    OR i.response_ms < 0
              )
        """,
    },
}


def scalar(conn, query: str, params: tuple) -> int:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "DQ SQL returned no result"
        )

    return int(row[0] or 0)


def load_issues(
    conn,
    base_query: str,
    source_batch_run_id: int,
    issue_limit: int,
):
    query = (
        base_query
        + """
          ORDER BY 3 NULLS LAST
          LIMIT %s
        """
    )

    with conn.cursor() as cur:
        cur.execute(
            query,
            (
                source_batch_run_id,
                issue_limit,
            ),
        )

        return cur.fetchall()


def count_issues(
    conn,
    base_query: str,
    source_batch_run_id: int,
) -> int:
    query = (
        "SELECT COUNT(*) "
        "FROM ("
        + base_query
        + ") dq_problem_rows"
    )

    return scalar(
        conn,
        query,
        (source_batch_run_id,),
    )


def main() -> None:
    source_batch_run_id = int(
        os.getenv("SOURCE_BATCH_RUN_ID", "0")
    )

    if source_batch_run_id <= 0:
        raise RuntimeError(
            "SOURCE_BATCH_RUN_ID is not set"
        )

    config = get_dwh_config()

    with connect(config) as conn:

        # ----------------------------------------------------
        # Validate DWH target
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Validate source batch
        # ----------------------------------------------------

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

        business_date, source_status = source_batch

        if source_status != "SUCCESS":
            raise RuntimeError(
                f"Source batch is not SUCCESS: "
                f"{source_status}"
            )

        # ----------------------------------------------------
        # Active DQ catalog
        # ----------------------------------------------------

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    rule_id,
                    rule_code,
                    rule_name,
                    severity,
                    category,
                    target_column,
                    issue_limit
                FROM dq.v_current_rule
                WHERE is_active = true
                ORDER BY rule_id
                """
            )

            catalog = cur.fetchall()

        if not catalog:
            raise RuntimeError(
                "No active DQ rules found"
            )

        for row in catalog:
            rule_code = row[1]

            if rule_code not in RULES:
                raise RuntimeError(
                    f"No implementation for rule: "
                    f"{rule_code}"
                )

        # ----------------------------------------------------
        # Create DQ run
        # ----------------------------------------------------

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dq.run (
                    source_batch_run_id,
                    business_date,
                    status,
                    started_at,
                    rules_total,
                    created_by,
                    message
                )
                VALUES (
                    %s,
                    %s,
                    'RUNNING',
                    now(),
                    %s,
                    'dq_runner',
                    %s
                )
                RETURNING dq_run_id
                """,
                (
                    source_batch_run_id,
                    business_date,
                    len(catalog),
                    (
                        f"CRM Retail row-level DQ; "
                        f"source_batch_run_id="
                        f"{source_batch_run_id}"
                    ),
                ),
            )

            dq_run_id = cur.fetchone()[0]

        # Freeze exact DQ rule versions used by this run.
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO dq.run_rule_snapshot (
                    dq_run_id,
                    rule_id
                )
                VALUES (
                    %s,
                    %s
                )
                """,
                [
                    (
                        int(dq_run_id),
                        int(row[0]),
                    )
                    for row in catalog
                ],
            )

        conn.commit()

        print("DQ RUN STARTED")
        print(f"dq_run_id    : {dq_run_id}")
        print(f"source batch : {source_batch_run_id}")
        print(f"business date: {business_date}")
        print(f"rules        : {len(catalog)}")
        print()

        passed = 0
        warned = 0
        failed = 0

        try:
            for (
                rule_id,
                rule_code,
                rule_name,
                severity,
                category,
                target_column,
                issue_limit,
            ) in catalog:

                impl = RULES[rule_code]

                checked_rows = scalar(
                    conn,
                    impl["checked_sql"],
                    (source_batch_run_id,),
                )

                failed_rows = count_issues(
                    conn,
                    impl["issue_sql"],
                    source_batch_run_id,
                )

                if failed_rows == 0:
                    result_status = "PASS"
                    passed += 1

                elif severity == "WARNING":
                    result_status = "WARNING"
                    warned += 1

                else:
                    result_status = "FAIL"
                    failed += 1

                # --------------------------------------------
                # Create aggregate result
                # --------------------------------------------

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO dq.result (
                            dq_run_id,
                            rule_id,
                            status,
                            checked_rows,
                            failed_rows,
                            metric_value,
                            expected_value,
                            details
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            0,
                            %s
                        )
                        RETURNING dq_result_id
                        """,
                        (
                            dq_run_id,
                            rule_id,
                            result_status,
                            checked_rows,
                            failed_rows,
                            failed_rows,
                            (
                                f"category={category}; "
                                f"severity={severity}; "
                                f"column="
                                f"{target_column or '-'}"
                            ),
                        ),
                    )

                    dq_result_id = cur.fetchone()[0]

                issues_saved = 0

                # --------------------------------------------
                # Save concrete failed rows
                # --------------------------------------------

                if failed_rows > 0:
                    issues = load_issues(
                        conn,
                        impl["issue_sql"],
                        source_batch_run_id,
                        int(issue_limit),
                    )

                    with conn.cursor() as cur:
                        for issue in issues:
                            (
                                source_schema,
                                source_object,
                                source_pk,
                                application_id,
                                applicant_id,
                                column_name,
                                bad_value,
                                expected_value,
                                error_code,
                                error_message,
                            ) = issue

                            cur.execute(
                                """
                                INSERT INTO dq.issue (
                                    dq_run_id,
                                    dq_result_id,
                                    rule_id,
                                    source_batch_run_id,
                                    severity,
                                    issue_status,
                                    source_system,
                                    source_schema,
                                    source_object,
                                    source_pk,
                                    application_id,
                                    applicant_id,
                                    column_name,
                                    bad_value,
                                    expected_value,
                                    error_code,
                                    error_message
                                )
                                VALUES (
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    'OPEN',
                                    'CRM_RETAIL',
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
                                    dq_run_id,
                                    dq_result_id,
                                    rule_id,
                                    source_batch_run_id,
                                    severity,
                                    source_schema,
                                    source_object,
                                    source_pk,
                                    application_id,
                                    applicant_id,
                                    column_name,
                                    bad_value,
                                    expected_value,
                                    error_code,
                                    error_message,
                                ),
                            )

                            issues_saved += 1

                # --------------------------------------------
                # Record how many details were persisted
                # --------------------------------------------

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE dq.result
                        SET details =
                            details
                            || %s
                        WHERE dq_result_id = %s
                        """,
                        (
                            (
                                f"; issues_saved="
                                f"{issues_saved}; "
                                f"issue_limit="
                                f"{issue_limit}"
                            ),
                            dq_result_id,
                        ),
                    )

                conn.commit()

                print(
                    f"{result_status:<7} "
                    f"{rule_code:<40} "
                    f"checked={checked_rows:<5} "
                    f"failed={failed_rows:<5} "
                    f"issues={issues_saved}",
                    flush=True,
                )

            # ------------------------------------------------
            # Finish run
            # ------------------------------------------------

            final_status = (
                "FAILED"
                if failed > 0
                else "SUCCESS"
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dq.run
                    SET
                        status = %s,
                        finished_at = now(),
                        rules_passed = %s,
                        rules_warned = %s,
                        rules_failed = %s,
                        message = %s
                    WHERE dq_run_id = %s
                    """,
                    (
                        final_status,
                        passed,
                        warned,
                        failed,
                        (
                            f"DQ completed; "
                            f"pass={passed}; "
                            f"warning={warned}; "
                            f"fail={failed}"
                        ),
                        dq_run_id,
                    ),
                )

            conn.commit()

            print()
            print(f"DQ RUN {final_status}")
            print(f"dq_run_id : {dq_run_id}")
            print(f"rules     : {len(catalog)}")
            print(f"pass      : {passed}")
            print(f"warning   : {warned}")
            print(f"fail      : {failed}")

            if failed > 0:
                raise SystemExit(2)

        except BaseException:
            conn.rollback()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status
                    FROM dq.run
                    WHERE dq_run_id = %s
                    """,
                    (dq_run_id,),
                )

                current = cur.fetchone()

                if (
                    current is not None
                    and current[0] == "RUNNING"
                ):
                    cur.execute(
                        """
                        UPDATE dq.run
                        SET
                            status = 'FAILED',
                            finished_at = now(),
                            rules_passed = %s,
                            rules_warned = %s,
                            rules_failed = %s,
                            message =
                                'DQ runner execution error'
                        WHERE dq_run_id = %s
                        """,
                        (
                            passed,
                            warned,
                            failed,
                            dq_run_id,
                        ),
                    )

            conn.commit()
            raise


if __name__ == "__main__":
    main()