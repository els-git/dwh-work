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


def connect_readonly(config: DatabaseConfig):
    """Read-only session on a SOURCE database (CRM) through the source gateway (I13-07):
    the server is in read-only mode, checked before the first query, and only reading
    statements are sent. Not for the DWH database: use connect()."""
    from dwh_api.source_gateway import connect as gateway_connect

    return gateway_connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.user,
        password=config.password,
        sslmode=config.sslmode,
        connect_timeout=10,
        application_name="dwh_crm_etl",
    )