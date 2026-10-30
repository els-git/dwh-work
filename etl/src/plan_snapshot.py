from __future__ import annotations

import os
from datetime import datetime, timedelta

from dwh.config import get_crm_config
from dwh.db import connect_readonly


def get_count(cur, sql: str, params=()) -> int:
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def main() -> None:
    snapshot_date = os.getenv("SNAPSHOT_DATE")

    if not snapshot_date:
        raise RuntimeError("SNAPSHOT_DATE is not set")

    start = datetime.fromisoformat(snapshot_date)
    end = start + timedelta(days=1)

    config = get_crm_config()

    with connect_readonly(config) as conn:
        with conn.cursor() as cur:

            cur.execute("SHOW transaction_read_only")
            read_only = cur.fetchone()[0]

            print("CRM SNAPSHOT PLAN")
            print(f"date      : {snapshot_date}")
            print(f"read only : {read_only}")
            print()

            applications = get_count(
                cur,
                """
                SELECT COUNT(*)
                FROM app_retail.application
                WHERE created >= %s
                  AND created < %s
                """,
                (start, end),
            )

            applicants = get_count(
                cur,
                """
                WITH selected_app AS (
                    SELECT
                        id,
                        applicant_id
                    FROM app_retail.application
                    WHERE created >= %s
                      AND created < %s
                )
                SELECT COUNT(*)
                FROM app_retail.applicant a
                WHERE
                    a.application_id IN (
                        SELECT id
                        FROM selected_app
                    )
                    OR
                    a.id IN (
                        SELECT applicant_id
                        FROM selected_app
                        WHERE applicant_id IS NOT NULL
                    )
                """,
                (start, end),
            )

            credit_info = get_count(
                cur,
                """
                WITH selected_app AS (
                    SELECT
                        id,
                        requested_credit_info_id,
                        chosen_credit_info_id,
                        final_credit_info_id
                    FROM app_retail.application
                    WHERE created >= %s
                      AND created < %s
                )
                SELECT COUNT(*)
                FROM app_retail.credit_info ci
                WHERE
                    ci.application_id IN (
                        SELECT id
                        FROM selected_app
                    )
                    OR
                    ci.id IN (
                        SELECT requested_credit_info_id
                        FROM selected_app
                        WHERE requested_credit_info_id IS NOT NULL

                        UNION

                        SELECT chosen_credit_info_id
                        FROM selected_app
                        WHERE chosen_credit_info_id IS NOT NULL

                        UNION

                        SELECT final_credit_info_id
                        FROM selected_app
                        WHERE final_credit_info_id IS NOT NULL
                    )
                """,
                (start, end),
            )

            workflow = get_count(
                cur,
                """
                WITH selected_app AS (
                    SELECT id
                    FROM app_retail.application
                    WHERE created >= %s
                      AND created < %s
                )
                SELECT COUNT(*)
                FROM app_retail.workflow_history wh
                WHERE wh.application_id IN (
                    SELECT id
                    FROM selected_app
                )
                """,
                (start, end),
            )

            integration = get_count(
                cur,
                """
                WITH selected_app AS (
                    SELECT
                        id,
                        applicant_id
                    FROM app_retail.application
                    WHERE created >= %s
                      AND created < %s
                ),
                selected_applicant AS (
                    SELECT a.id
                    FROM app_retail.applicant a
                    WHERE
                        a.application_id IN (
                            SELECT id
                            FROM selected_app
                        )
                        OR
                        a.id IN (
                            SELECT applicant_id
                            FROM selected_app
                            WHERE applicant_id IS NOT NULL
                        )
                )
                SELECT COUNT(*)
                FROM integration_retail.integration_log il
                WHERE
                    il.application_id IN (
                        SELECT id
                        FROM selected_app
                    )
                    OR
                    il.applicant_id IN (
                        SELECT id
                        FROM selected_applicant
                    )
                """,
                (start, end),
            )

            print("APPLICATION COHORT")
            print(f"application       : {applications}")
            print(f"applicant         : {applicants}")
            print(f"credit_info       : {credit_info}")
            print(f"workflow_history  : {workflow}")
            print(f"integration_log   : {integration}")
            print()

            reference_tables = [
                ("product", "app_retail.product"),
                ("stage", "app_retail.stage"),
                ("status", "app_retail.status"),
                ("currency", "app_retail.dir_currency"),
                ("sales_channel", "app_retail.dir_sales_channel"),
                ("branch", "app_retail.dir_branch"),
                ("users", "app_retail.users"),
            ]

            print("REFERENCE TABLES")

            for name, table in reference_tables:
                count = get_count(
                    cur,
                    f"SELECT COUNT(*) FROM {table}"
                )

                print(f"{name:<16}: {count}")


if __name__ == "__main__":
    main()