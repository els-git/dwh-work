from __future__ import annotations

import psycopg

from dwh.config import DatabaseConfig


def connect(config: DatabaseConfig) -> psycopg.Connection:
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.user,
        password=config.password,
        sslmode=config.sslmode,
        connect_timeout=10,
    )


def connect_readonly(config: DatabaseConfig) -> psycopg.Connection:
    conn = connect(config)

    # Must be set before the first transaction starts.
    conn.read_only = True

    return conn