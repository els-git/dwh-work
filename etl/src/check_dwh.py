from __future__ import annotations

from dwh.config import get_dwh_config
from dwh.db import connect


def main() -> None:
    config = get_dwh_config()

    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_database(),
                    current_user,
                    inet_server_addr(),
                    inet_server_port(),
                    version()
                """
            )

            row = cur.fetchone()

    print("DWH CONNECTION OK")
    print(f"database : {row[0]}")
    print(f"user     : {row[1]}")
    print(f"address  : {row[2]}")
    print(f"port     : {row[3]}")
    print(f"version  : {row[4]}")


if __name__ == "__main__":
    main()