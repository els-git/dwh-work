"""PostgreSQL connector adapter (I04-01), contract of I03-04.

Every session is opened through the source gateway (I13-07, source_gateway.py):
read-only on the server, checked before the first query, and only reading
statements are sent; connect and statement timeouts. Only the catalog is read in
I04: no table rows. Errors are reported as short codes with fixed messages;
server error texts, connection strings and credentials are never returned.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from dwh_api import source_gateway
from dwh_api.connector_contract import (
    ConnectionTestResult,
    ConnectorError,
    DiscoveredObject,
    ReadRequest,
    SecretValue,
    register_adapter,
)

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MS = 30_000
DISCOVER_LIMIT = 5000
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")

MESSAGES = {
    "OK": "Connected (read-only session)",
    "CONFIG_INVALID": "Connection settings are incomplete or invalid",
    "NO_CREDENTIAL": "The source has no active credential",
    "AUTH_FAILED": "Authentication failed: check the user name and the credential",
    "HOST_NOT_FOUND": "Host name could not be resolved",
    "CONNECTION_REFUSED": "Connection refused by the host or port",
    "CONNECT_TIMEOUT": "Connection timed out",
    "DATABASE_NOT_FOUND": "Database does not exist on the server",
    "SSL_ERROR": "SSL negotiation failed: check the SSL mode",
    "ACCESS_DENIED": "The user has no access to this database",
    "NOT_READ_ONLY": "The session could not be made read-only",
    "WRITE_BLOCKED": "Statement refused: only reading is allowed on a source",
    "QUERY_TIMEOUT": "The catalog query timed out",
    "CONNECT_FAILED": "Connection failed",
}

# SQLSTATE first: server messages may be localised (e.g. Russian lc_messages).
_SQLSTATE_CODES = {
    "28P01": "AUTH_FAILED",          # invalid_password
    "28000": "ACCESS_DENIED",        # invalid_authorization_specification (pg_hba, unknown role)
    "3D000": "DATABASE_NOT_FOUND",   # invalid_catalog_name
    "42501": "ACCESS_DENIED",        # insufficient_privilege
    "57014": "QUERY_TIMEOUT",        # query_canceled (statement_timeout)
    "53300": "CONNECT_FAILED",       # too_many_connections
}

# Connection failures carry no SQLSTATE in psycopg, only text. The server may
# answer in English or in Russian (lc_messages); every needle must be present.
_ERROR_PATTERNS = (
    (("password authentication failed",), "AUTH_FAILED"),
    (("проверку подлинности",), "AUTH_FAILED"),
    (("no password supplied",), "AUTH_FAILED"),
    (("database \"", "does not exist"), "DATABASE_NOT_FOUND"),
    (("база данных", "не существует"), "DATABASE_NOT_FOUND"),
    (("role \"", "does not exist"), "ACCESS_DENIED"),
    (("роль", "не существует"), "ACCESS_DENIED"),
    (("pg_hba.conf",), "ACCESS_DENIED"),
    (("could not translate host name",), "HOST_NOT_FOUND"),
    (("name or service not known",), "HOST_NOT_FOUND"),
    (("no such host",), "HOST_NOT_FOUND"),
    (("connection refused",), "CONNECTION_REFUSED"),
    (("actively refused",), "CONNECTION_REFUSED"),
    (("отверг запрос",), "CONNECTION_REFUSED"),
    (("timeout expired",), "CONNECT_TIMEOUT"),
    (("timed out",), "CONNECT_TIMEOUT"),
    (("ssl",), "SSL_ERROR"),
)


class PostgresConnectorError(ConnectorError):
    def __init__(self, code: str) -> None:
        self.code = code if code in MESSAGES else "CONNECT_FAILED"
        super().__init__(MESSAGES[self.code])


def classify_error(exc: BaseException) -> str:
    code = _SQLSTATE_CODES.get(getattr(exc, "sqlstate", None) or "")
    if code:
        return code
    text = str(exc).lower()
    for needles, code in _ERROR_PATTERNS:
        if all(needle in text for needle in needles):
            return code
    return "CONNECT_FAILED"


def connection_kwargs(config: Mapping[str, Any], credential: SecretValue | None) -> dict[str, Any]:
    host = config.get("host")
    database = config.get("database")
    user = config.get("username")
    port = config.get("port", 5432)
    sslmode = config.get("sslmode") or "prefer"
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise PostgresConnectorError("CONFIG_INVALID") from None
    if (not isinstance(host, str) or not host.strip() or not isinstance(database, str) or not database.strip()
            or not isinstance(user, str) or not user.strip() or not 1 <= port <= 65535 or sslmode not in SSL_MODES):
        raise PostgresConnectorError("CONFIG_INVALID")
    if credential is None:
        raise PostgresConnectorError("NO_CREDENTIAL")
    return {
        "host": host.strip(), "port": port, "dbname": database.strip(), "user": user.strip(),
        "password": credential.reveal(), "sslmode": sslmode,
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "application_name": "dwh_connector",
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
    }


OBJECTS_SQL = """
SELECT n.nspname AS schema, c.relname AS name,
       CASE c.relkind WHEN 'r' THEN 'TABLE' WHEN 'p' THEN 'PARTITIONED_TABLE' WHEN 'v' THEN 'VIEW'
                      WHEN 'm' THEN 'MATERIALIZED_VIEW' WHEN 'f' THEN 'FOREIGN_TABLE' END AS object_type
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%%'
   AND NOT c.relispartition
 ORDER BY n.nspname, c.relname
 LIMIT %s
"""

OBJECT_SQL = """
SELECT c.oid, c.relkind,
       CASE c.relkind WHEN 'r' THEN 'TABLE' WHEN 'p' THEN 'PARTITIONED_TABLE' WHEN 'v' THEN 'VIEW'
                      WHEN 'm' THEN 'MATERIALIZED_VIEW' WHEN 'f' THEN 'FOREIGN_TABLE' ELSE 'OTHER' END AS object_type
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = %s AND c.relname = %s
"""

# Same column description as etl/src/capture_source_metadata.py (CRM snapshot).
COLUMNS_SQL = """
SELECT a.attnum AS ordinal_position, a.attname AS column_name,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted_type, t.typname AS udt_name,
       NOT a.attnotnull AS is_nullable,
       EXISTS (SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid = a.attrelid AND i.indisprimary
                  AND a.attnum = ANY (i.indkey)) AS is_primary_key,
       EXISTS (SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid = a.attrelid AND i.indisunique
                  AND a.attnum = ANY (i.indkey)) AS is_unique_key,
       (a.attidentity <> '') AS is_identity, (a.attgenerated <> '') AS is_generated,
       pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) AS default_expression,
       pg_catalog.col_description(a.attrelid, a.attnum) AS column_comment
  FROM pg_catalog.pg_attribute a
  JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
  LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
 WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum
"""


def _gateway_code(exc: source_gateway.SourceGatewayError) -> str:
    return "NOT_READ_ONLY" if exc.code == source_gateway.SOURCE_NOT_READ_ONLY else "WRITE_BLOCKED"


class PostgresAdapter:
    adapter_code = "POSTGRESQL"
    source_kind = "DATABASE"

    def __init__(self, connect=psycopg.connect) -> None:
        self._connect = connect

    def _open(self, config: Mapping[str, Any], credential: SecretValue | None):
        kwargs = connection_kwargs(config, credential)
        try:
            return source_gateway.connect(row_factory=dict_row, connect_fn=self._connect, **kwargs)
        except source_gateway.SourceGatewayError as exc:
            raise PostgresConnectorError(_gateway_code(exc)) from None
        except psycopg.Error as exc:
            raise PostgresConnectorError(classify_error(exc)) from None
        finally:
            kwargs.pop("password", None)

    def _run(self, conn, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except source_gateway.SourceGatewayError as exc:
            raise PostgresConnectorError(_gateway_code(exc)) from None
        except psycopg.Error as exc:
            raise PostgresConnectorError(classify_error(exc)) from None

    def test_connection(self, config: Mapping[str, Any], credential: SecretValue | None) -> ConnectionTestResult:
        try:
            conn = self._open(config, credential)
        except PostgresConnectorError as exc:
            return ConnectionTestResult(False, MESSAGES[exc.code], exc.code)
        try:
            version = self._run(conn, "SELECT current_setting('server_version') AS v")[0]["v"]
        except PostgresConnectorError as exc:
            return ConnectionTestResult(False, MESSAGES[exc.code], exc.code)
        finally:
            conn.rollback()
            conn.close()
        return ConnectionTestResult(True, MESSAGES["OK"], "OK", f"PostgreSQL {version}"[:200])

    def discover(self, config: Mapping[str, Any], credential: SecretValue | None,
                 limit: int = DISCOVER_LIMIT) -> list[DiscoveredObject]:
        conn = self._open(config, credential)
        try:
            rows = self._run(conn, OBJECTS_SQL, (int(limit),))
        finally:
            conn.rollback()
            conn.close()
        return [DiscoveredObject(r["schema"], r["name"], r["object_type"]) for r in rows]

    def describe(self, config: Mapping[str, Any], credential: SecretValue | None,
                 objects: Sequence[tuple[str, str]]) -> tuple[str, dict[tuple[str, str], dict[str, Any]]]:
        """Server version and, per (schema, name), relkind/object_type/columns.

        Objects that do not exist in the source are absent from the result.
        """
        conn = self._open(config, credential)
        found: dict[tuple[str, str], dict[str, Any]] = {}
        try:
            version = self._run(conn, "SELECT current_setting('server_version') AS v")[0]["v"]
            for schema, name in objects:
                rows = self._run(conn, OBJECT_SQL, (schema, name))
                if not rows:
                    continue
                columns = self._run(conn, COLUMNS_SQL, (rows[0]["oid"],))
                found[(schema, name)] = {
                    "relkind": rows[0]["relkind"], "object_type": rows[0]["object_type"],
                    "columns": [
                        {**c, "column_name": str(c["column_name"]), "udt_name": str(c["udt_name"])} for c in columns
                    ],
                }
        finally:
            conn.rollback()
            conn.close()
        return f"PostgreSQL {version}"[:200], found

    def read(self, config: Mapping[str, Any], credential: SecretValue | None,
             request: ReadRequest) -> Iterator[dict[str, Any]]:
        raise ConnectorError("Reading table rows is not available in I04")


ADAPTER = PostgresAdapter()
register_adapter(ADAPTER)
