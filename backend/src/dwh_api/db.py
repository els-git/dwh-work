from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from dwh_api.config import ApiConfig


def connect_readonly(
    config: ApiConfig,
) -> psycopg.Connection:
    """
    Create a PostgreSQL connection for the API.

    Security rules:
    - dedicated dwh_app role
    - localhost DWH
    - transaction read-only
    - dict rows for JSON responses
    """

    conn = psycopg.connect(
        host=config.db_host,
        port=config.db_port,
        dbname=config.db_name,
        user=config.db_user,
        password=config.db_password,
        row_factory=dict_row,
        connect_timeout=5,
        application_name="dwh_fastapi",
    )

    # Defense in depth:
    # even if DB permissions are accidentally widened later,
    # this API connection remains read-only.
    conn.read_only = True

    return conn

def connect_control(
    config: ApiConfig,
) -> psycopg.Connection:
    """
    Create a controlled read/write API connection.

    The connection still uses the restricted dwh_app
    PostgreSQL role.

    Direct writes to security tables are prohibited
    by DB privileges. Only explicitly granted
    SECURITY DEFINER functions may perform writes.
    """

    conn = psycopg.connect(
        host=config.db_host,
        port=config.db_port,
        dbname=config.db_name,
        user=config.db_user,
        password=config.db_password,
        row_factory=dict_row,
        connect_timeout=5,
        application_name="dwh_fastapi_control",
    )

    return conn