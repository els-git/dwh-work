from __future__ import annotations

from dwh.config import get_crm_config
from dwh.db import connect_readonly


def main() -> None:
    config = get_crm_config()

    with connect_readonly(config) as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    current_database(),
                    current_user,
                    inet_server_addr(),
                    inet_server_port(),
                    current_setting('transaction_read_only')
                """
            )

            db, user, address, port, read_only = cur.fetchone()

            print("CRM CONNECTION OK")
            print(f"database          : {db}")
            print(f"user              : {user}")
            print(f"address           : {address}")
            print(f"port              : {port}")
            print(f"transaction readonly: {read_only}")

            cur.execute(
                """
                SELECT
                    has_table_privilege(
                        current_user,
                        'app_retail.application',
                        'SELECT'
                    ),
                    has_table_privilege(
                        current_user,
                        'app_retail.application',
                        'INSERT'
                    ),
                    has_table_privilege(
                        current_user,
                        'app_retail.application',
                        'UPDATE'
                    ),
                    has_table_privilege(
                        current_user,
                        'app_retail.application',
                        'DELETE'
                    ),
                    has_table_privilege(
                        current_user,
                        'app_retail.application',
                        'TRUNCATE'
                    )
                """
            )

            privileges = cur.fetchone()

            print()
            print("app_retail.application privileges")
            print(f"SELECT   : {privileges[0]}")
            print(f"INSERT   : {privileges[1]}")
            print(f"UPDATE   : {privileges[2]}")
            print(f"DELETE   : {privileges[3]}")
            print(f"TRUNCATE : {privileges[4]}")

            cur.execute(
                """
                SELECT COUNT(*)
                FROM app_retail.application
                """
            )

            application_count = cur.fetchone()[0]

            print()
            print(f"application total : {application_count}")

            cur.execute(
                """
                SELECT
                    created::date AS business_date,
                    COUNT(*) AS application_count
                FROM app_retail.application
                WHERE created >= CURRENT_DATE - INTERVAL '14 days'
                GROUP BY created::date
                ORDER BY business_date DESC
                """
            )

            rows = cur.fetchall()

            print()
            print("Recent application counts:")
            for business_date, count in rows:
                print(f"{business_date} : {count}")


if __name__ == "__main__":
    main()