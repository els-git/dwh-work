"""DM mart from a ready SQL query (I23, package 023; decisions C2-C6). Shared by the API and the engine.

The query is run only by the role dwh_dm_sql_ro (package 023): SELECT on schema ods and nothing else,
read-only transactions. Protection comes from the rights of the role, not from parsing the text; the text
checks below only give clear messages:
  - one query: SELECT, WITH ... SELECT or a compound one (UNION / INTERSECT / EXCEPT), a trailing ';' is
    dropped; the query is always wrapped as SELECT * FROM (<query>) AS q, so a second statement or a data
    modifying WITH is a syntax error of the database;
  - EXPLAIN (FORMAT JSON, VERBOSE): every relation read is in schema ods (C2); these tables refresh the mart
    after their ODS step (C7);
  - result columns: names [a-z][a-z0-9_]* (not dm_loaded_at, unique) and types mapped to the field types of
    a mart (C5); exactly one key column is chosen by the user.
The connection password is DWH_DM_SQL_DB_PASSWORD (/etc/dwh/dwh.env, set by tools/server/dwh_dm_sql_setup.py).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row

ROLE = "dwh_dm_sql_ro"
PASSWORD_ENV = "DWH_DM_SQL_DB_PASSWORD"
MAX_SQL_CHARS = 50000
PREVIEW_ROWS = 50
MAX_COLUMNS = 300
COLUMN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
RESERVED = {"dm_loaded_at"}
_LEADING = re.compile(r"^\s*(?:--[^\n]*(?:\n|$)\s*|/\*.*?\*/\s*)*", re.S)
_FIRST_WORD = re.compile(r"^(\(|[A-Za-z]+)")
_NUMERIC = re.compile(r"^numeric\((\d+),(\d+)\)$")
_VARCHAR = re.compile(r"^(?:character varying|varchar|character|char|bpchar)\((\d+)\)$")
_SIMPLE_TYPES = {
    "bigint": "bigint", "integer": "integer", "smallint": "smallint", "numeric": "numeric",
    "real": "numeric", "double precision": "numeric", "date": "date", "boolean": "boolean",
    "timestamp without time zone": "timestamp", "timestamp": "timestamp",
    "timestamp with time zone": "timestamptz", "timestamptz": "timestamptz",
    "int8": "bigint", "int4": "integer", "int2": "smallint", "bool": "boolean",
    "float4": "numeric", "float8": "numeric",
}


class SqlMartError(ValueError):
    """The query cannot be used; code is the technical reason (shown with the message)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Column:
    name: str
    source_type: str
    type: str
    problem: str | None = None


@dataclass
class CheckResult:
    sql_text: str
    tables: list[str]
    columns: list[Column]
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return [f"{c.name}: {c.problem}" for c in self.columns if c.problem]


def normalize(sql_text: str | None) -> str:
    """The stored text: trimmed, without trailing ';'. Raises SqlMartError for an unusable text."""
    text = (sql_text or "").strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    if not text:
        raise SqlMartError("DM_SQL_EMPTY", "the query is empty")
    if len(text) > MAX_SQL_CHARS:
        raise SqlMartError("DM_SQL_TOO_LONG", f"the query is longer than {MAX_SQL_CHARS} characters")
    head = _LEADING.sub("", text)
    word = _FIRST_WORD.match(head)
    if not word or word.group(1).lower() not in ("(", "select", "with"):
        raise SqlMartError("DM_SQL_NOT_SELECT", "only one SELECT / WITH ... SELECT query (UNION, INTERSECT, EXCEPT "
                                                "allowed) can build a mart")
    return text


def wrapped(sql_text: str) -> str:
    """The query as a subquery; new lines keep a trailing line comment of the text inside it."""
    return f"SELECT * FROM (\n{sql_text}\n) AS q"


def map_type(source_type: str) -> str:
    """Field type of the mart (whitelist of package 018) for a result column type (format_type)."""
    t = source_type.strip().lower()
    if t in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[t]
    m = _NUMERIC.match(t)
    if m:
        p, s = int(m.group(1)), int(m.group(2))
        return f"numeric({p},{s})" if 1 <= p <= 99 and 0 <= s <= 99 else "numeric"
    m = _VARCHAR.match(t)
    if m and 1 <= int(m.group(1)) <= 9999:
        return f"varchar({int(m.group(1))})"
    return "text"


def cast_type(field_type: str) -> str:
    """Type to cast to while reading: varchar(n) is read as text, so a longer value fails on the insert instead
    of being cut by the cast."""
    return "text" if field_type.startswith("varchar") else field_type


def plan_tables(plan: Any) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "Relation Name" in node:
                found.add((str(node.get("Schema", "")), str(node["Relation Name"])))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plan)
    return found


def password() -> str:
    value = os.environ.get(PASSWORD_ENV, "").strip()
    if not value:
        raise SqlMartError("DM_SQL_NOT_CONFIGURED", f"{ROLE} is not configured ({PASSWORD_ENV})")
    return value


def connect(host: str, port: int | str, dbname: str, timeout_ms: int = 30000) -> psycopg.Connection:
    """Connection of dwh_dm_sql_ro: read-only, statement timeout (also set on the role by package 023)."""
    conn = psycopg.connect(host=host, port=port, dbname=dbname, user=ROLE, password=password(),
                           row_factory=dict_row, connect_timeout=5, application_name="dwh_dm_sql",
                           options=f"-c default_transaction_read_only=on -c statement_timeout={int(timeout_ms)}")
    conn.read_only = True
    return conn


def _database_error(exc: psycopg.Error) -> SqlMartError:
    diag = getattr(exc, "diag", None)
    state = getattr(diag, "sqlstate", None) or ""
    message = (getattr(diag, "message_primary", None) or str(exc) or type(exc).__name__).strip()[:1000]
    if state == "42501":
        return SqlMartError("DM_SQL_NOT_ALLOWED", f"the query reads data outside schema ods: {message}")
    if state == "25006":
        return SqlMartError("DM_SQL_NOT_ALLOWED", f"the query tries to change data: {message}")
    if state == "57014":
        return SqlMartError("DM_SQL_TIMEOUT", f"the query ran too long: {message}")
    return SqlMartError("DM_SQL_INVALID", f"PostgreSQL: {message}")


def check(conn: psycopg.Connection, sql_text: str, preview_rows: int = PREVIEW_ROWS) -> CheckResult:
    """Checks the query on a dwh_dm_sql_ro connection: tables of the plan, columns and types, first rows.
    The transaction is rolled back. Raises SqlMartError."""
    text = normalize(sql_text)
    query = wrapped(text)
    try:
        with conn.cursor() as cur:
            cur.execute("EXPLAIN (FORMAT JSON, VERBOSE) " + query)
            row = cur.fetchone()
            plan = row["QUERY PLAN"] if isinstance(row, dict) else row[0]
            relations = plan_tables(plan)
            outside = sorted(f"{s}.{r}" for s, r in relations if s != "ods")
            if outside:
                raise SqlMartError("DM_SQL_NOT_ALLOWED", "the query may read only schema ods: " + ", ".join(outside))
            tables = sorted({r for s, r in relations if s == "ods"})
            if not tables:
                raise SqlMartError("DM_SQL_NO_TABLE", "the query reads no table of schema ods")
            if len(tables) > 50:
                raise SqlMartError("DM_SQL_TOO_MANY_TABLES", "the query reads more than 50 tables")
            cur.execute(query + f" LIMIT {int(preview_rows)}")
            description = cur.description or []
            result = cur.pgresult
            oids = [result.ftype(i) for i in range(len(description))] if result is not None else []
            mods = [result.fmod(i) for i in range(len(description))] if result is not None else []
            rows = [dict(r) for r in cur.fetchall()]
            # format_type() with the type modifier: numeric(18,2), character varying(20) (the column description
            # of the driver has no modifiers).
            cur.execute("SELECT format_type(t.oid, t.mod) AS typ FROM unnest(%s::oid[], %s::int[]) WITH ORDINALITY "
                        "AS t(oid, mod, n) ORDER BY t.n", (oids, mods))
            type_names = [r["typ"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
    except SqlMartError:
        conn.rollback()
        raise
    except psycopg.Error as exc:
        conn.rollback()
        raise _database_error(exc) from exc
    conn.rollback()
    if len(description) > MAX_COLUMNS:
        raise SqlMartError("DM_SQL_TOO_MANY_COLUMNS", f"the query returns more than {MAX_COLUMNS} columns")
    columns: list[Column] = []
    seen: set[str] = set()
    for i, d in enumerate(description):
        source_type = type_names[i] if i < len(type_names) and type_names[i] else (d.type_display or "text")
        col = Column(d.name, source_type, map_type(source_type))
        if not COLUMN_NAME.match(d.name):
            col.problem = "name must be lower-case latin letters, digits and _ (give the column an alias: AS name)"
        elif d.name in RESERVED:
            col.problem = "name dm_loaded_at is reserved for the load time"
        elif d.name in seen:
            col.problem = "name occurs more than once"
        seen.add(d.name)
        columns.append(col)
    return CheckResult(text, tables, columns, rows)


def fields_document(result: CheckResult, key: str, titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Fields for app.sql_mart_save from a checked query (never from the client) and the chosen key."""
    if result.problems:
        raise SqlMartError("DM_SQL_COLUMNS_INVALID", "columns of the query: " + "; ".join(result.problems[:20]))
    names = [c.name for c in result.columns]
    if key not in names:
        raise SqlMartError("DM_SQL_KEY_INVALID", "the key must be one column of the query")
    if len(names) < 2:
        raise SqlMartError("DM_SQL_COLUMNS_INVALID", "the query needs at least one column besides the key")
    titles = titles or {}
    return [{"name": c.name, "title": (str(titles.get(c.name) or "").strip()[:200] or None), "type": c.type,
             "is_key": c.name == key} for c in result.columns]


def qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_sql(sql_text: str, fields: list[tuple[str, str]]) -> str:
    """SELECT of the mart rows from the stored query: field columns cast to their types, then the load time
    (columns in the order of the INSERT / COPY of the engine)."""
    exprs = [f"CAST(q.{qi(name)} AS {cast_type(data_type)}) AS {qi(name)}" for name, data_type in fields]
    return "SELECT " + ", ".join(exprs) + ", now() AS \"dm_loaded_at\" FROM (\n" + sql_text + "\n) AS q"


def key_null_sql(sql_text: str, key: str) -> str:
    return f"SELECT count(*) AS n FROM (\n{sql_text}\n) AS q WHERE q.{qi(key)} IS NULL"


def key_duplicates_sql(sql_text: str, key: str, examples: int = 10) -> str:
    return (f"SELECT q.{qi(key)}::text AS key_value, count(*) AS rows, count(*) OVER () AS duplicate_keys "
            f"FROM (\n{sql_text}\n) AS q GROUP BY q.{qi(key)} HAVING count(*) > 1 ORDER BY count(*) DESC, 1 "
            f"LIMIT {int(examples)}")
