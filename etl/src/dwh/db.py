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

