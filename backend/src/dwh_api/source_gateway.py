"""I13-07: the only way DWH opens a connection to a source database.

Requirement of the user (05.11.2026): no outgoing request of DWH may change
anything in a source; databases are only read (SELECT), APIs only with GET.
Two layers of the plan I06-05 (decision G1), both here:

1. the server of the source is put into read-only mode
   (``default_transaction_read_only=on`` at connect and ``BEGIN READ ONLY`` by
   the driver) and ``SHOW transaction_read_only`` must answer ``on`` before the
   first query of the caller, otherwise SOURCE_NOT_READ_ONLY (fail-closed);
2. every statement is checked before it is sent: only reading statements pass,
   anything else raises SOURCE_WRITE_BLOCKED and nothing reaches the source.

The third layer, a source account with SELECT only, is the owner's (I13-06).
A unit test (test_source_gateway.py) checks that source connections are not
opened anywhere else in backend/src and etl/src.

Error texts never contain the statement, its values or connection settings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import psycopg

SOURCE_WRITE_BLOCKED = "SOURCE_WRITE_BLOCKED"
SOURCE_NOT_READ_ONLY = "SOURCE_NOT_READ_ONLY"

MESSAGES = {
    SOURCE_WRITE_BLOCKED: "Statement refused by the source gateway: only reading is allowed",
    SOURCE_NOT_READ_ONLY: "Source session is not read-only",
}

# First word of an allowed statement.
ALLOWED_START = frozenset({"SELECT", "WITH", "SHOW", "FETCH", "CLOSE", "DECLARE", "VALUES"})

# Words that never appear in a reading statement (outside literals and quoted names).
FORBIDDEN_WORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "TRUNCATE", "COPY", "CREATE", "ALTER", "DROP", "GRANT",
    "REVOKE", "CALL", "DO", "LOCK", "VACUUM", "ANALYZE", "ANALYSE", "REINDEX", "CLUSTER", "REFRESH", "IMPORT",
    "LISTEN", "NOTIFY", "UNLISTEN", "PREPARE", "EXECUTE", "DEALLOCATE", "DISCARD", "RESET", "SET", "SECURITY",
    "COMMENT", "CHECKPOINT", "LOAD", "INTO", "SHARE", "NOWAIT",
})

# Functions with side effects even in a read-only transaction (or that change the session).
FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(set_config|nextval|setval|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile"
    r"|pg_switch_wal|pg_create_restore_point|pg_promote|pg_(try_)?advisory\w*|pg_notify|lo_\w+|dblink\w*"
    r"|pg_file_write|pg_file_unlink|pg_file_rename|pg_logdir_ls|pg_stat_reset\w*|pg_import_system_collations"
    r"|txid_current|pg_current_xact_id)\s*\(",
    re.IGNORECASE,
)

_LITERALS = re.compile(
    r"""(?sx)
      --[^\n]*                      # line comment
    | /\*.*?\*/                     # block comment
    | (?:[EeBbXxUu]&?)?'(?:[^']|'')*'  # string literal
    | \$(?P<tag>[A-Za-z_]?\w*)\$.*?\$(?P=tag)\$  # dollar-quoted literal
    | "(?:[^"]|"")*"                # quoted identifier
    """
)


class SourceGatewayError(Exception):
    """Refusal of the gateway; ``code`` is SOURCE_WRITE_BLOCKED or SOURCE_NOT_READ_ONLY."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = MESSAGES[code]
        super().__init__(self.message)


def check_statement(text: str) -> None:
    """Raise SOURCE_WRITE_BLOCKED unless ``text`` is one reading statement."""
    if not isinstance(text, str) or FORBIDDEN_FUNCTIONS.search(_LITERALS.sub(" ", text)):
        raise SourceGatewayError(SOURCE_WRITE_BLOCKED)
    stripped = _LITERALS.sub(" ", text).strip().rstrip(";").strip()
    if not stripped or ";" in stripped:
        raise SourceGatewayError(SOURCE_WRITE_BLOCKED)
    words = [w.upper() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", stripped)]
    match = re.match(r"[(\s]*([A-Za-z_]+)", stripped)
    first = match.group(1).upper() if match else ""
    if first not in ALLOWED_START or any(w in FORBIDDEN_WORDS for w in words):
        raise SourceGatewayError(SOURCE_WRITE_BLOCKED)
    if first == "DECLARE" and not re.search(r"\bCURSOR\b.*\bFOR\s*\(?\s*(SELECT|WITH|VALUES)\b", stripped,
                                            re.IGNORECASE | re.DOTALL):
        raise SourceGatewayError(SOURCE_WRITE_BLOCKED)


def _text(query: Any, raw) -> str:
    if isinstance(query, str):
        return query
    if isinstance(query, bytes):
        return query.decode("utf-8", "replace")
    as_string = getattr(query, "as_string", None)
    if as_string is None:
        raise SourceGatewayError(SOURCE_WRITE_BLOCKED)
    return as_string(raw)


class GatewayCursor:
    """Cursor that sends only checked reading statements; no COPY, no executemany."""

    def __init__(self, cursor, raw) -> None:
        self._cursor = cursor
        self._raw = raw

    def __enter__(self) -> GatewayCursor:
        return self

    def __exit__(self, *exc) -> None:
        self._cursor.close()

    def execute(self, query: Any, params: Sequence[Any] | None = None) -> GatewayCursor:
        check_statement(_text(query, self._raw))
        self._cursor.execute(query, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchmany(self, size: int = 0):
        return self._cursor.fetchmany(size) if size else self._cursor.fetchmany()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._cursor)

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def close(self) -> None:
        self._cursor.close()


class GatewayConnection:
    """Read-only source session: cursor(), execute(), commit(), rollback(), close() only."""

    read_only = True

    def __init__(self, raw) -> None:
        self._raw = raw

    def __enter__(self) -> GatewayConnection:
        return self

    def __exit__(self, exc_type, *exc) -> None:
        try:
            self._raw.rollback()
        finally:
            self._raw.close()

    def cursor(self, *, row_factory=None) -> GatewayCursor:
        raw_cursor = self._raw.cursor(row_factory=row_factory) if row_factory is not None else self._raw.cursor()
        return GatewayCursor(raw_cursor, self._raw)

    def execute(self, query: Any, params: Sequence[Any] | None = None) -> GatewayCursor:
        cur = self.cursor()
        return cur.execute(query, params)

    def commit(self) -> None:
        # Nothing is written; ending the read-only transaction is harmless.
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    @property
    def closed(self) -> bool:
        return bool(self._raw.closed)


def connect(*, host: str, port: int, dbname: str, user: str, password: str | None, sslmode: str = "prefer",
            connect_timeout: int = 10, application_name: str = "dwh_source", statement_timeout_ms: int | None = None,
            row_factory=None, connect_fn: Callable[..., Any] = psycopg.connect) -> GatewayConnection:
    """Open a read-only source session; psycopg errors of connecting pass through unchanged."""
    options = "-c default_transaction_read_only=on"
    if statement_timeout_ms is not None:
        options += f" -c statement_timeout={int(statement_timeout_ms)}"
    kwargs: dict[str, Any] = {
        "host": host, "port": port, "dbname": dbname, "user": user, "password": password, "sslmode": sslmode,
        "connect_timeout": connect_timeout, "application_name": application_name, "options": options,
        "autocommit": False,
    }
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    raw = connect_fn(**kwargs)
    kwargs.clear()
    try:
        raw.read_only = True
        with raw.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            row = cur.fetchone()
        value = next(iter(row.values())) if isinstance(row, dict) else (row[0] if row else None)
        raw.rollback()
        if value != "on":
            raise SourceGatewayError(SOURCE_NOT_READ_ONLY)
    except BaseException:
        raw.close()
        raise
    return GatewayConnection(raw)
