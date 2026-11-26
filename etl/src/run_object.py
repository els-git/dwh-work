"""Run of one published source object: source -> target table of its configuration (I06).

Plan I06 (approved 04.11.2026): full read of the object, validation of the whole set
before any write, then ONE transaction that applies it to the target table by the
business key and the write rules of the published version, writes the record history
and closes the run. No code per source: everything comes from the version in force.

  1. etl.object_run_begin(object)   checks the object (READY, not legacy, connection
                                     not disabled), locks it for this session, opens
                                     batch_run (OBJECT_LOAD) / job_run / object_run.
  2. credential                      SecretStore READ by the ETL role (package 007).
  3. structure                       every mapped source column exists with the published
                                     type (else SCHEMA_DRIFT) and so does every target column
                                     (else TARGET_DRIFT); nothing is read before that.
  4. read                            planner estimate checked first; values as text, in batches,
                                     into a temporary text table, at most ROW_LIMIT rows (R6);
                                     progress is printed. Every value is checked against its
                                     target type (pg_input_is_valid, I13-08); any invalid value
                                     stops the run with a field-by-field report; only then the
                                     typed temporary table is filled (CAST by PostgreSQL).
  5. validation                      key without NULL and without duplicates.
  6. apply (one transaction)         new key -> INSERT (AUTHORITATIVE and INSERT_ONLY
                                     fields); existing key -> UPDATE of AUTHORITATIVE fields
                                     that changed; NULL clears a value only with
                                     allow_explicit_null; equal rows are skipped; no deletes
                                     (R2). Every INSERT/UPDATE gets a history row (R5);
                                     etl.object_run_finish(SUCCESS) in the same transaction.
  Any error: rollback of all target writes, then the run is closed FAILED with a code.

Messages never contain credentials. Source values appear only in the conversion report of
I13-08 (decision R1 = B: up to 5 invalid values per field with counts and up to 10 keys); every
other message is built from codes only. The report goes into etl.error_log (message, column,
first key, bad_value, diagnostic_context) through the finish functions of package 014.

ODS step (I06-05, package 012): after a successful STG run, an object with a published ODS
version is transformed from its STG table into its ODS table inside the DWH (no source access):
etl.object_ods_run_begin -> staging with the white list transformations of the version (SQL built
here from quoted names and literals only) -> key validation -> the same apply by key as above with
the ODS history -> etl.object_ods_run_finish, one transaction. The ODS step is a run of its own: an
ODS failure never changes the committed STG result. DWH_RUN_OBJECT_STAGE=ODS runs the ODS step alone.

I21 (plan docs/DWH_I21_LARGE_TABLES_PLAN_APPROVED_20261118.md, B1-B6): steps 4-6 run per CHUNK of
DWH_LOAD_CHUNK_ROWS rows (default 100 000), each chunk committed on its own; the source is read by key when a
unique index covers the key (one short query per chunk), otherwise one cursor is cut into chunks; no row limit
(R6) any more, a free space check instead; a new row is recorded in the history by its key only; a failure keeps
the committed chunks (the run is FAILED with their counts, a rerun completes the load); the watermark is written
after the last chunk; staging uses COPY (DWH_STAGE_COPY=0: batched INSERTs). The ODS step is cut by the primary
key of the STG table the same way.

Usage: set DWH_RUN_OBJECT_ID and run (tools\\run_etl.ps1 -Task run-object -ObjectId <id> [-Stage ODS]).
"""

from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from dwh_api.source_gateway import SourceGatewayError  # backend/src is on PYTHONPATH (tools/run_etl.ps1)

ROW_LIMIT = 1_000_000  # I06 R6; I21: no longer applied, kept for callers that pass row_limit
# I21 (B1): the object is loaded in chunks; each chunk is checked, applied and committed on its own.
CHUNK_ROWS = 100_000
FETCH_SIZE = 5_000
SOURCE_CONNECT_TIMEOUT = 15
SOURCE_STATEMENT_TIMEOUT_MS = 30 * 60 * 1000
# I08 step 8 (11.11.2026): a FULL read waited on the source for an hour in DECLARE. A lock held by another
# session of the source now fails the run after SOURCE_LOCK_TIMEOUT_MS (LOCK_TIMEOUT); a statement the source
# does not answer at all is cancelled by a watchdog after DWH_SOURCE_REPLY_TIMEOUT_SECONDS (SOURCE_NO_RESPONSE).
SOURCE_LOCK_TIMEOUT_MS = 60 * 1000
SOURCE_REPLY_TIMEOUT_SECONDS = 300
# 12.11.2026: on the PC the cancel request did not help either (the first FETCH got no answer after DECLARE was
# answered): the connection is then broken after SOURCE_ABORT_GRACE_SECONDS more.
SOURCE_ABORT_GRACE_SECONDS = 30


class RunError(Exception):
    """A run failure with a stable code; never contains secrets. Only a conversion report
    (I13-08) carries source values: message, bad_value and diagnostic."""

    def __init__(self, code: str, message: str, *, record_key: dict | None = None, column: str | None = None,
                 bad_value: str | None = None, diagnostic: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.record_key = record_key
        self.column = column
        self.bad_value = bad_value
        self.diagnostic = diagnostic


@dataclass
class RunResult:
    run_id: int
    batch_run_id: int
    status: str
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    rows_stale: int = 0
    rows_deleted: int = 0
    load_strategy: str = "FULL"
    watermark_from: str | None = None
    watermark_to: str | None = None
    error_code: str | None = None
    message: str | None = None


@dataclass
class Plan:
    """What the published definition means for the apply step."""

    target_schema: str
    target_table: str
    fields: list[dict]
    key: list[str]
    strategy: dict | None = None
    columns: list[str] = field(init=False)
    authoritative: list[dict] = field(init=False)
    load_strategy: str = field(init=False)
    change_field: dict | None = field(init=False)
    overlap_minutes: int = field(init=False)
    delete_flag: str | None = field(init=False)

    def __post_init__(self) -> None:
        if not self.fields or not self.key:
            raise RunError("CONFIG_INVALID", "Published configuration has no fields or no key")
        names = [f["target_column"] for f in self.fields]
        if any(k not in names for k in self.key):
            raise RunError("CONFIG_INVALID", "Key column is not in the field map")
        self.columns = [f["target_column"] for f in self.fields
                        if f["write_policy"] != "NO_WRITE" and f.get("source_column")]
        if any(k not in self.columns for k in self.key):
            raise RunError("CONFIG_INVALID", "Key column is not written")
        self.authoritative = [f for f in self.fields if f["write_policy"] == "AUTHORITATIVE"
                              and f.get("source_column") and f["target_column"] not in self.key]
        # I07 (package 011): load strategy of the published version.
        strategy = self.strategy or {}
        self.load_strategy = strategy.get("load_strategy") or "FULL"
        change = strategy.get("change_column")
        self.change_field = next((f for f in self.written if f["target_column"] == change), None) if change else None
        if change and self.change_field is None:
            raise RunError("CONFIG_INVALID", "Change column is not a written field")
        if self.change_field is not None:
            from dwh_api.field_types import parse

            if parse(self.change_field["source_type"])[0] not in ("DATE", "TIMESTAMP", "TIMESTAMPTZ", "INTEGER", "BIGINT"):
                raise RunError("CONFIG_INVALID", "Change column has an unsupported source type")
        if self.load_strategy == "INCREMENTAL" and self.change_field is None:
            raise RunError("CONFIG_INVALID", "Incremental strategy without a change column")
        self.overlap_minutes = int(strategy.get("overlap_minutes") or 0)
        self.delete_flag = strategy.get("delete_flag_column")
        if self.delete_flag and self.delete_flag not in self.columns:
            raise RunError("CONFIG_INVALID", "Delete flag is not a written field")

    @property
    def written(self) -> list[dict]:
        return [f for f in self.fields if f["target_column"] in self.columns]


# ------------------------------------------------------------------ errors


_COPY_COLUMN = re.compile(r'column (\w+)')

_SQLSTATE_CODES = {
    "22001": "VALUE_TOO_LONG",
    "22003": "NUMERIC_OUT_OF_RANGE",
    "22007": "TYPE_CONVERSION",
    "22008": "TYPE_CONVERSION",
    "22P02": "TYPE_CONVERSION",
    "22023": "TYPE_CONVERSION",
    "22018": "TYPE_CONVERSION",
    "23502": "TARGET_NOT_NULL",
    "23505": "TARGET_UNIQUE",
    "23503": "TARGET_REFERENCE",
    "23514": "TARGET_CHECK",
    "57014": "TIMEOUT",
    "40P01": "DEADLOCK",
    "55P03": "LOCK_TIMEOUT",
}


def database_error(exc: psycopg.Error, stage: str) -> RunError:
    """Sanitized RunError: the code and the column, never the server message (it may quote values)."""
    state = getattr(getattr(exc, "diag", None), "sqlstate", None) or ""
    code = _SQLSTATE_CODES.get(state)
    if code is None:
        code = "SOURCE_ERROR" if stage == "source" else "DATABASE_ERROR"
    column = getattr(getattr(exc, "diag", None), "column_name", None)
    if column is None:
        context = getattr(getattr(exc, "diag", None), "context", None) or ""
        match = _COPY_COLUMN.search(context)
        column = match.group(1) if match else None
    # Without SQLSTATE the error comes from the driver, not from the server: name its class (no message text).
    message = f"{stage}: {code} (SQLSTATE {state or 'unknown, ' + type(exc).__name__})" + (
        f", column {column}" if column else "")
    return RunError(code, message, column=column)


# ------------------------------------------------------------------ conversion report (I13-08)


REPORT_VALUES = 5
REPORT_KEYS = 10
REPORT_VALUE_CHARS = 50
REPORT_COLUMNS = 4


# jsonb_build_object takes at most 100 arguments (SQLSTATE 54023): 50 key/value pairs per call, joined by ||.
JSONB_PAIRS_PER_CALL = 50


def _jsonb_object(pairs: list[sql.Composable]) -> sql.Composable:
    """jsonb object of "'name', value" pairs for any number of columns (wide tables, I18)."""
    if not pairs:
        return sql.SQL("'{}'::jsonb")
    chunks = [pairs[i:i + JSONB_PAIRS_PER_CALL] for i in range(0, len(pairs), JSONB_PAIRS_PER_CALL)]
    return sql.SQL(" || ").join(sql.SQL("jsonb_build_object({})").format(sql.SQL(", ").join(c)) for c in chunks)


def _shown(value: str) -> str:
    return value if len(value) <= REPORT_VALUE_CHARS else value[:REPORT_VALUE_CHARS] + "…"


def _key_text(key: dict) -> str:
    parts = [f"{k}={v}" for k, v in key.items()]
    return parts[0] if len(parts) == 1 else "(" + ", ".join(parts) + ")"


def conversion_error(stage: str, total: int, fields: list[dict]) -> RunError:
    """RunError TYPE_CONVERSION with the report of every failed field.

    fields: [{"column", "rule", "invalid_rows", "values": [{"value", "count"}], "keys": [dict]}]."""
    parts = []
    for f in fields:
        text = f"{f['column']} ({f['rule']}): {f['invalid_rows']} of {total} rows invalid"
        if f["values"]:
            text += "; values: " + ", ".join(f"'{_shown(v['value'])}' x{v['count']}" for v in f["values"])
            if f.get("more_values"):
                text += ", ..."
        if f["keys"]:
            text += "; keys: " + ", ".join(_key_text(k) for k in f["keys"])
            if f["invalid_rows"] > len(f["keys"]):
                text += f" ({len(f['keys'])} of {f['invalid_rows']})"
        parts.append(text)
    first = fields[0]
    diagnostic = {"kind": "TYPE_CONVERSION", "stage": stage, "total_rows": total,
                  "fields": [{k: f[k] for k in ("column", "rule", "invalid_rows", "values", "keys")} for f in fields]}
    return RunError("TYPE_CONVERSION", " | ".join(parts), column=first["column"],
                    record_key=first["keys"][0] if first["keys"] else None,
                    bad_value=first["values"][0]["value"] if first["values"] else None, diagnostic=diagnostic)


def _report_field(cur, relation: sql.Composable, invalid: sql.Composable, value: sql.Composable | None,
                  key_json: sql.Composable, key_order: sql.Composable) -> tuple[list[dict], bool, list[dict]]:
    """Most frequent invalid values (with counts) and the first keys of the invalid rows."""
    values: list[dict] = []
    more = False
    if value is not None:
        cur.execute(sql.SQL("SELECT {v}, count(*) FROM {r} WHERE {i} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {n}").format(
            v=value, r=relation, i=invalid, n=sql.Literal(REPORT_VALUES + 1)))
        rows = cur.fetchall()
        more = len(rows) > REPORT_VALUES
        values = [{"value": "" if v is None else str(v), "count": c} for v, c in rows[:REPORT_VALUES]]
    cur.execute(sql.SQL("SELECT {k} FROM {r} WHERE {i} ORDER BY {o} LIMIT {n}").format(
        k=key_json, r=relation, i=invalid, o=key_order, n=sql.Literal(REPORT_KEYS)))
    keys = [row[0] for row in cur.fetchall()]
    return values, more, keys


# ------------------------------------------------------------------ source


def source_connect(connection_config: dict, password: str):
    """Read-only session on the source through the source gateway (I13-07); settings only from the
    published connection."""
    from dwh_api import source_gateway

    try:
        return source_gateway.connect(
            host=connection_config.get("host"), port=int(connection_config.get("port") or 5432),
            dbname=connection_config.get("database"), user=connection_config.get("username"),
            password=password, sslmode=connection_config.get("sslmode") or "prefer",
            connect_timeout=SOURCE_CONNECT_TIMEOUT, application_name="dwh_object_run",
            statement_timeout_ms=SOURCE_STATEMENT_TIMEOUT_MS, lock_timeout_ms=SOURCE_LOCK_TIMEOUT_MS,
            keepalives=True,
        )
    except source_gateway.SourceGatewayError as exc:
        raise RunError(exc.code, exc.message) from None
    except psycopg.Error as exc:
        raise RunError("SOURCE_CONNECT_FAILED", "Source connection failed (SQLSTATE "
                       + (getattr(getattr(exc, "diag", None), "sqlstate", None) or "unknown") + ")") from None


def source_columns(conn, schema: str, table: str) -> dict[str, str]:
    """Normalized types of the source object's columns (pg_catalog, no data)."""
    from dwh_api.field_types import normalize_postgres

    with conn.cursor() as cur:
        cur.execute(
            """SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                 FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""",
            (schema, table),
        )
        rows = cur.fetchall()
    conn.rollback()
    return {name: normalize_postgres(type_) for name, type_ in rows}


def check_structure(plan: Plan, columns: dict[str, str]) -> None:
    if not columns:
        raise RunError("SOURCE_OBJECT_MISSING", "Source object not found or not readable")
    for f in plan.written:
        actual = columns.get(f["source_column"])
        if actual is None:
            raise RunError("SCHEMA_DRIFT", "Mapped source column is missing", column=f["source_column"])
        if actual != f["source_type"]:
            raise RunError("SCHEMA_DRIFT", f"Source type changed: published {f['source_type']}, now {actual}",
                           column=f["source_column"])


def check_target(dwh, plan: Plan) -> None:
    """The target table still has every written column with the published type."""
    from dwh_api.field_types import normalize_postgres

    with dwh.cursor() as cur:
        cur.execute(
            """SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                 FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped""",
            (plan.target_schema, plan.target_table),
        )
        actual = {name: normalize_postgres(type_) for name, type_ in cur.fetchall()}
    for f in plan.written:
        type_ = actual.get(f["target_column"])
        if type_ is None:
            raise RunError("TARGET_DRIFT", "Target column is missing", column=f["target_column"])
        if type_ != f["target_type"]:
            raise RunError("TARGET_DRIFT", f"Target type changed: published {f['target_type']}, now {type_}",
                           column=f["target_column"])


def estimated_rows(conn, schema: str, table: str) -> int | None:
    """Planner estimate (pg_class.reltuples, no data); None when the table was never analysed."""
    with conn.cursor() as cur:
        cur.execute("""SELECT c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = %s AND c.relname = %s""", (schema, table))
        row = cur.fetchone()
    conn.rollback()
    return None if row is None or row[0] is None or row[0] < 0 else int(row[0])


def increment_filter(plan: Plan, watermark: str | None) -> sql.Composable | None:
    """D3: rows with change value >= watermark - overlap (inclusive); None = read everything."""
    from dwh_api.field_types import parse, postgres_type

    if plan.load_strategy != "INCREMENTAL" or watermark is None:
        return None
    f = plan.change_field
    column = sql.Identifier(f["source_column"])
    if parse(f["source_type"])[0] in ("INTEGER", "BIGINT"):
        return sql.SQL("{} >= CAST({} AS bigint)").format(column, sql.Literal(watermark))
    return sql.SQL("{} >= CAST({} AS {}) - make_interval(mins => {})").format(
        column, sql.Literal(watermark), sql.SQL(postgres_type(f["source_type"])), sql.Literal(plan.overlap_minutes))


def reply_timeout_seconds() -> int:
    value = os.environ.get("DWH_SOURCE_REPLY_TIMEOUT_SECONDS", "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else SOURCE_REPLY_TIMEOUT_SECONDS


class ReplyWatchdog:
    """A source statement without answer: after `seconds` a PostgreSQL cancel request; if the statement still
    waits `grace` seconds later, the connection is broken (the answer is lost on the way)."""

    def __init__(self, conn, seconds: int, grace: int = SOURCE_ABORT_GRACE_SECONDS,
                 log: Callable[[str], None] = lambda message: None) -> None:
        self.conn = conn
        self.seconds = seconds
        self.grace = grace
        self.log = log
        self.fired: str | None = None
        self.aborted = False
        self._active = 0
        self._lock = threading.Lock()

    def run(self, cur, statement: sql.Composable, what: str):
        with self._lock:
            self._active += 1
            ticket = self._active
        timer = threading.Timer(self.seconds, self._fire, args=(what, ticket))
        timer.daemon = True
        timer.start()
        try:
            cur.execute(statement)
            return cur.fetchall() if what == "FETCH" else None
        finally:
            timer.cancel()
            with self._lock:
                self._active += 1  # the statement is over: a late timer of this ticket does nothing

    def _waiting(self, ticket: int) -> bool:
        with self._lock:
            return self._active == ticket

    def _fire(self, what: str, ticket: int) -> None:
        if not self._waiting(ticket):
            return
        self.fired = what
        self.log(f"STAGE: source gave no answer to {what} in {self.seconds} s, cancel request sent")
        try:
            self.conn.cancel()
        except Exception:  # noqa: BLE001 - the cancel request is best effort; the abort follows
            pass
        abort = threading.Timer(self.grace, self._abort, args=(what, ticket))
        abort.daemon = True
        abort.start()

    def _abort(self, what: str, ticket: int) -> None:
        if not self._waiting(ticket):
            return
        self.aborted = True
        self.log(f"STAGE: cancel had no effect in {self.grace} s, source connection closed")
        try:
            self.conn.abort()
        except Exception:  # noqa: BLE001 - the run limit of the dispatcher stays the last guard
            pass


def source_wait_error(exc: psycopg.Error, watchdog: ReplyWatchdog, schema: str, table: str) -> RunError | None:
    state = getattr(getattr(exc, "diag", None), "sqlstate", None) or ""
    if state == "55P03":
        return RunError("LOCK_TIMEOUT", f"source: table {schema}.{table} is locked by another session of the source "
                        f"for more than {SOURCE_LOCK_TIMEOUT_MS // 1000} s (SQLSTATE 55P03); end that session "
                        "or its open transaction and run again")
    if watchdog.aborted:
        return RunError("SOURCE_NO_RESPONSE", f"source: no answer to {watchdog.fired} on {schema}.{table} within "
                        f"{watchdog.seconds} s, the cancel request had no effect in {watchdog.grace} s, connection "
                        "closed; the answer of the source was lost on the way (network, VPN or connection pooler)")
    if watchdog.fired is not None and state == "57014":
        return RunError("SOURCE_NO_RESPONSE", f"source: no answer to {watchdog.fired} on {schema}.{table} within "
                        f"{watchdog.seconds} s, statement cancelled (SQLSTATE 57014); the source server or its "
                        "connection pooler did not respond")
    return None


def chunk_rows() -> int:
    value = os.environ.get("DWH_LOAD_CHUNK_ROWS", "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else CHUNK_ROWS


def source_key_order(conn, schema: str, table: str, columns: list[str]) -> tuple[list[str], bool] | None:
    """I21 (B2): a unique index on exactly these columns (no expression, no predicate) allows reading the source by
    key: (columns in index order, primary key). None — no such index (a view, or no index): one cursor."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT i.indisprimary, array_agg(a.attname::text ORDER BY k.n)
                     FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid
                     JOIN pg_namespace ns ON ns.oid = t.relnamespace
                     CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY k(attnum, n)
                     JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                    WHERE ns.nspname = %s AND t.relname = %s AND i.indisunique AND i.indisvalid
                      AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts = %s
                    GROUP BY i.indexrelid, i.indisprimary
                    ORDER BY i.indisprimary DESC""",
                (schema, table, len(columns)))
            rows = cur.fetchall()
        conn.rollback()
    except Exception:  # noqa: BLE001 - without the catalog answer the source is read with one cursor
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    for primary, names in rows:
        if sorted(names) == sorted(columns):
            return list(names), bool(primary)
    return None


def source_chunks(conn, schema: str, table: str, plan: Plan, where: sql.Composable | None,
                  log: Callable[[str], None] = lambda message: None, size: int | None = None):
    """I21: lists of at most `size` source rows (text values in plan.written order).

    With a unique index on the key (B2) every chunk is its own short query: rows after the last key of the
    previous chunk, in key order, LIMIT size; nothing stays open on the source between chunks. Otherwise (a view,
    no index) one cursor is read and cut into chunks."""
    size = size or chunk_rows()
    key_source = {f["target_column"]: f["source_column"] for f in plan.written}
    columns = [key_source[k] for k in plan.key]
    order = source_key_order(conn, schema, table, columns)
    if order is None:
        log("STAGE: source read with one cursor (no unique index on the key)")
        chunk: list = []
        for row in read_source(conn, schema, table, plan, None, where, log):
            chunk.append(row)
            if len(chunk) >= size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk
        return
    names, primary = order
    log(f"STAGE: source read by key ({', '.join(names)}), {size} rows per chunk")
    position = [next(i for i, f in enumerate(plan.written) if f["source_column"] == n) for n in names]
    select = sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(sql.SQL("{}::text").format(sql.Identifier(f["source_column"])) for f in plan.written),
        sql.Identifier(schema), sql.Identifier(table))
    key_row = sql.SQL("ROW({})").format(sql.SQL(", ").join(sql.Identifier(n) for n in names))
    order_by = sql.SQL(", ").join(sql.Identifier(n) for n in names)
    watchdog = ReplyWatchdog(conn, reply_timeout_seconds(), log=log)
    last: list | None = None
    try:
        with conn.cursor() as cur:
            if not primary:  # a unique index admits NULL keys, which reading by key would skip
                nulls = sql.SQL("SELECT count(*) FROM {}.{} WHERE {}").format(
                    sql.Identifier(schema), sql.Identifier(table),
                    sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(sql.Identifier(n)) for n in names))
                if where is not None:
                    nulls = nulls + sql.SQL(" AND ({})").format(where)
                (count,), = watchdog.run(cur, nulls, "FETCH")
                conn.rollback()
                if count:
                    raise RunError("KEY_NULL", f"{count} source rows have an empty business key: "
                                   + ", ".join(names) + " (unique index of the source allows NULL)")
            while True:
                conditions = [sql.SQL("({})").format(where)] if where is not None else []
                if last is not None:
                    conditions.append(sql.SQL("{} > ROW({})").format(
                        key_row, sql.SQL(", ").join(sql.Literal(v) for v in last)))
                statement = select
                if conditions:
                    statement = statement + sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
                statement = statement + sql.SQL(" ORDER BY {} LIMIT {}").format(order_by, sql.Literal(size))
                rows = watchdog.run(cur, statement, "FETCH")
                conn.rollback()
                if not rows:
                    return
                last = [rows[-1][i] for i in position]
                yield rows
                if len(rows) < size:
                    return
    except psycopg.Error as exc:
        raise (source_wait_error(exc, watchdog, schema, table) or database_error(exc, "source")) from None
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            if not watchdog.aborted:
                raise


def read_source(conn, schema: str, table: str, plan: Plan, row_limit: int | None, where: sql.Composable | None = None,
                log: Callable[[str], None] = lambda message: None, reply_timeout: int | None = None):
    """Rows of the mapped columns as text (NULL stays NULL), in published field order.

    The cursor is declared and fetched with plain SQL statements (DECLARE / FETCH) on an
    ordinary client cursor: a psycopg server-side cursor sends a separate portal Describe,
    which hung behind the connection pooler of the user's source on 05.11.2026.
    """
    select = sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(sql.SQL("{}::text").format(sql.Identifier(f["source_column"])) for f in plan.written),
        sql.Identifier(schema), sql.Identifier(table))
    if where is not None:
        select = select + sql.SQL(" WHERE ") + where
    count = 0
    watchdog = ReplyWatchdog(conn, reply_timeout or reply_timeout_seconds(), log=log)
    try:
        with conn.cursor() as cur:
            watchdog.run(cur, sql.SQL("DECLARE dwh_object_run NO SCROLL CURSOR FOR {}").format(select), "DECLARE")
            fetch = sql.SQL("FETCH FORWARD {} FROM dwh_object_run").format(sql.Literal(FETCH_SIZE))
            first = True
            while True:
                batch = watchdog.run(cur, fetch, "FETCH")
                if first:
                    log(f"STAGE: source answered, first batch {len(batch)} rows")
                    first = False
                if not batch:
                    break
                for row in batch:
                    count += 1
                    if row_limit is not None and count > row_limit:
                        raise RunError("ROW_LIMIT", f"Source object has more than {row_limit} rows")
                    yield row
    except psycopg.Error as exc:
        raise (source_wait_error(exc, watchdog, schema, table) or database_error(exc, "source")) from None
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            if not watchdog.aborted:
                raise


# ------------------------------------------------------------------ DWH apply


def _ident(name: str) -> sql.Identifier:
    return sql.Identifier(name)


STAGE_BATCH = 1_000
# One bind parameter per value; the PostgreSQL protocol allows at most 65535 per statement (I18, wide tables).
MAX_BIND_PARAMETERS = 65_535


def stage_copy_enabled() -> bool:
    return os.environ.get("DWH_STAGE_COPY", "").strip() != "0"


def stage_batch_rows(columns: int) -> int:
    """Rows per INSERT so that rows x columns stays within the parameter limit."""
    return max(1, min(STAGE_BATCH, MAX_BIND_PARAMETERS // max(1, columns)))


def stage_rows(dwh, plan: Plan, rows, log: Callable[[str], None] = lambda m: None) -> int:
    """Source text values -> temporary text table -> check against the target types -> temporary
    table with the target types (explicit CAST by PostgreSQL).

    Plain multi-row INSERTs in batches (no COPY: a COPY to the DWH interleaved with the source
    FETCHes hung on the user's Windows PC on 05.11.2026).
    """
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("CREATE TEMP TABLE dwh_object_run_raw ({}) ON COMMIT DROP").format(
            sql.SQL(", ").join(sql.SQL("{} text").format(_ident(f["target_column"])) for f in plan.written)))
        row_sql = sql.SQL("({})").format(sql.SQL(", ").join([sql.Placeholder()] * len(plan.written)))
        head = sql.SQL("INSERT INTO dwh_object_run_raw ({}) VALUES ").format(
            sql.SQL(", ").join(_ident(c) for c in plan.columns))
        batch: list = []
        count = 0
        batch_rows = stage_batch_rows(len(plan.written))
        if stage_copy_enabled():
            # I21 (B6): COPY into the chunk table; DWH_STAGE_COPY=0 returns to batched INSERTs.
            with cur.copy(sql.SQL("COPY dwh_object_run_raw ({}) FROM STDIN").format(
                    sql.SQL(", ").join(_ident(c) for c in plan.columns))) as copy:
                for row in rows:
                    copy.write_row(row)
                    count += 1
            rows = ()

        def flush():
            if batch:
                cur.execute(head + sql.SQL(", ").join([row_sql] * len(batch)), [v for r in batch for v in r])
                batch.clear()

        for row in rows:
            batch.append(row)
            count += 1
            if len(batch) >= batch_rows:
                flush()
                if count % 50_000 < batch_rows:
                    log(f"STAGE: {count} rows staged")
        flush()
    check_values(dwh, plan, count)
    with dwh.cursor() as cur:
        from dwh_api.field_types import postgres_type

        types = [postgres_type(f["target_type"]) for f in plan.written]
        cur.execute(sql.SQL("CREATE TEMP TABLE dwh_object_run_stage ({}) ON COMMIT DROP").format(
            sql.SQL(", ").join(sql.SQL("{} {}").format(_ident(f["target_column"]), sql.SQL(t))
                               for f, t in zip(plan.written, types))))
        cur.execute(sql.SQL("INSERT INTO dwh_object_run_stage ({}) SELECT {} FROM dwh_object_run_raw").format(
            sql.SQL(", ").join(_ident(c) for c in plan.columns),
            sql.SQL(", ").join(sql.SQL("CAST({} AS {})").format(_ident(f["target_column"]), sql.SQL(t))
                               for f, t in zip(plan.written, types))))
    return count


def check_values(dwh, plan: Plan, total: int) -> None:
    """Every staged text value against its target type (pg_input_is_valid, PostgreSQL 16+): wrong
    format, number out of range, text longer than varchar(n). Raises the conversion report."""
    from dwh_api.field_types import postgres_type

    if total == 0:
        return
    checks = [(f, sql.SQL("{c} IS NOT NULL AND NOT pg_input_is_valid({c}, {t})").format(
        c=_ident(f["target_column"]), t=sql.Literal(postgres_type(f["target_type"])))) for f in plan.written]
    relation = sql.SQL("dwh_object_run_raw")
    key_json = _jsonb_object(list(sql.SQL("{}, {}").format(sql.Literal(k), _ident(k)) for k in plan.key))
    key_order = sql.SQL(", ").join(_ident(k) for k in plan.key)
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("SELECT {} FROM dwh_object_run_raw").format(
            sql.SQL(", ").join(sql.SQL("count(*) FILTER (WHERE {})").format(c) for _, c in checks)))
        counts = cur.fetchone()
        failed = []
        for (f, invalid), n in zip(checks, counts):
            if not n:
                continue
            values, more, keys = _report_field(cur, relation, invalid, _ident(f["target_column"]), key_json, key_order)
            failed.append({"column": f["target_column"],
                           "rule": f"{f['source_type']} -> {postgres_type(f['target_type'])}",
                           "invalid_rows": n, "values": values, "more_values": more, "keys": keys})
    if failed:
        raise conversion_error("STG", total, failed)


IDENTIFIER_SUFFIXES = ("_id", "_code", "_no", "_number", "_num", "_uuid", "_guid")


def _identifier_columns(plan: Plan) -> list[str]:
    """Columns that name a row without personal data: id, *_id, *_code, *_no ... (not the key itself)."""
    return [f["target_column"] for f in plan.fields
            if f["target_column"] not in plan.key
            and (f["target_column"] == "id" or f["target_column"].endswith(IDENTIFIER_SUFFIXES))][:REPORT_COLUMNS]


def null_key_error(cur, plan: Plan, nulls: int) -> RunError:
    """KEY_NULL with what is empty (per key column) and the identifier columns of the first rows (I18)."""
    empty_in = sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(_ident(k)) for k in plan.key)
    cur.execute(sql.SQL("SELECT {} FROM dwh_object_run_stage").format(
        sql.SQL(", ").join(sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(_ident(k)) for k in plan.key)))
    per_key = [{"column": k, "empty_rows": n} for k, n in zip(plan.key, cur.fetchone()) if n]
    identifiers = _identifier_columns(plan)
    rows: list[dict] = []
    if identifiers:
        cur.execute(sql.SQL("SELECT jsonb_build_object({}) FROM dwh_object_run_stage WHERE {} ORDER BY {} LIMIT {}").format(
            sql.SQL(", ").join(sql.SQL("{}, {}").format(sql.Literal(c), _ident(c)) for c in identifiers),
            empty_in, sql.SQL(", ").join(_ident(c) for c in identifiers), sql.Literal(REPORT_KEYS)))
        rows = [{k: (None if v is None else _shown(str(v))) for k, v in r[0].items()} for r in cur.fetchall()]
    text = f"{nulls} source rows have an empty business key: " + ", ".join(
        f"{p['column']} empty in {p['empty_rows']}" for p in per_key)
    if rows:
        text += "; rows: " + ", ".join(_key_text(r) for r in rows)
        if nulls > len(rows):
            text += f" ({len(rows)} of {nulls})"
    elif not identifiers:
        text += "; no identifier column (id, *_id, *_code, *_no) to name the rows"
    return RunError("KEY_NULL", text, column=per_key[0]["column"] if per_key else None,
                    record_key=rows[0] if rows else None,
                    diagnostic={"kind": "KEY_NULL", "empty_rows": nulls, "key_columns": per_key, "rows": rows})


def validate_key(dwh, plan: Plan) -> None:
    key = sql.SQL(", ").join(_ident(k) for k in plan.key)
    key_json = _jsonb_object(list(sql.SQL("{}, {}").format(sql.Literal(k), _ident(k)) for k in plan.key))
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM dwh_object_run_stage WHERE {}").format(
            sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(_ident(k)) for k in plan.key)))
        nulls = cur.fetchone()[0]
        if nulls:
            raise null_key_error(cur, plan, nulls)
        cur.execute(sql.SQL("SELECT {}, count(*) FROM dwh_object_run_stage GROUP BY {} HAVING count(*) > 1 LIMIT 1").format(
            key_json, key))
        dup = cur.fetchone()
        if dup:
            raise RunError("KEY_DUPLICATE", f"Business key occurs {dup[1]} times in the source set", record_key=dup[0])


def _changed(f: dict) -> sql.Composable:
    """Condition: the AUTHORITATIVE field of an existing row must be written."""
    col = _ident(f["target_column"])
    differs = sql.SQL("s.{c} IS DISTINCT FROM t.{c}").format(c=col)
    if f.get("allow_explicit_null"):
        return differs
    return sql.SQL("(s.{c} IS NOT NULL AND {d})").format(c=col, d=differs)


def apply_rows(dwh, plan: Plan, run_id: int,
               history: tuple[str, str] = ("object_record_change", "run_id")) -> tuple[int, int, int, int]:
    """INSERT new keys, UPDATE changed AUTHORITATIVE fields, history rows.

    Returns (inserted, updated, stale, deleted). With a change column, a row whose change value
    in the source is older than in the DWH is skipped as stale (D4). With a delete flag, an update
    that sets the flag to true is recorded as DELETE (soft delete, D5)."""
    target = sql.SQL("{}.{}").format(_ident(plan.target_schema), _ident(plan.target_table))
    join = sql.SQL(" AND ").join(sql.SQL("t.{k} = s.{k}").format(k=_ident(k)) for k in plan.key)
    key_json = _jsonb_object(list(sql.SQL("{}, s.{}").format(sql.Literal(k), _ident(k)) for k in plan.key))
    updated = stale = deleted = 0
    history_insert = sql.SQL("INSERT INTO etl.{}({}, change_kind, record_key, changes) ").format(
        _ident(history[0]), _ident(history[1]))
    fresh = sql.SQL("TRUE")
    if plan.change_field is not None:
        c = _ident(plan.change_field["target_column"])
        fresh = sql.SQL("(t.{c} IS NULL OR s.{c} IS NULL OR s.{c} >= t.{c})").format(c=c)
    with dwh.cursor() as cur:
        if plan.change_field is not None:
            cur.execute(sql.SQL("SELECT count(*) FROM dwh_object_run_stage s JOIN {target} t ON {join} WHERE NOT {fresh}").format(
                target=target, join=join, fresh=fresh))
            stale = cur.fetchone()[0]
        if plan.authoritative:
            changes = sql.SQL(" || ").join(
                sql.SQL("CASE WHEN {cond} THEN jsonb_build_object({name}, jsonb_build_object('before', to_jsonb(t.{c}), "
                        "'after', to_jsonb(s.{c}))) ELSE '{{}}'::jsonb END").format(
                    cond=_changed(f), name=sql.Literal(f["target_column"]), c=_ident(f["target_column"]))
                for f in plan.authoritative)
            cur.execute(sql.SQL(
                "CREATE TEMP TABLE dwh_object_run_diff ON COMMIT DROP AS "
                "SELECT {key_cols}, {key_json} AS record_key, {changes} AS changes "
                "FROM dwh_object_run_stage s JOIN {target} t ON {join} WHERE {fresh}").format(
                key_cols=sql.SQL(", ").join(sql.SQL("s.{}").format(_ident(k)) for k in plan.key),
                key_json=key_json, changes=changes, target=target, join=join, fresh=fresh))
            cur.execute("DELETE FROM dwh_object_run_diff WHERE changes = '{}'::jsonb")
            kind = sql.SQL("'UPDATE'")
            if plan.delete_flag:
                deleting = sql.SQL("(changes -> {f} ->> 'after')::boolean IS TRUE "
                                   "AND (changes -> {f} ->> 'before')::boolean IS NOT TRUE").format(
                    f=sql.Literal(plan.delete_flag))
                kind = sql.SQL("CASE WHEN {d} THEN 'DELETE' ELSE 'UPDATE' END").format(d=deleting)
                # The history table is append-only for the ETL role: count on the diff table.
                cur.execute(sql.SQL("SELECT count(*) FROM dwh_object_run_diff WHERE {d}").format(d=deleting))
                deleted = cur.fetchone()[0]
            cur.execute(history_insert + sql.SQL(
                "SELECT {run}, {kind}, record_key, changes FROM dwh_object_run_diff").format(
                run=sql.Literal(run_id), kind=kind))
            updated = cur.rowcount
            if updated:
                cur.execute(sql.SQL("UPDATE {target} t SET {sets} FROM dwh_object_run_stage s "
                                    "JOIN dwh_object_run_diff d ON {djoin} WHERE {join}").format(
                    target=target,
                    sets=sql.SQL(", ").join(sql.SQL("{c} = CASE WHEN {cond} THEN s.{c} ELSE t.{c} END").format(
                        c=_ident(f["target_column"]), cond=_changed(f)) for f in plan.authoritative),
                    djoin=sql.SQL(" AND ").join(sql.SQL("d.{k} = s.{k}").format(k=_ident(k)) for k in plan.key),
                    join=join))
                if cur.rowcount != updated:
                    raise RunError("APPLY_MISMATCH", "Updated rows differ from the history rows")
        new_rows = sql.SQL("FROM dwh_object_run_stage s WHERE NOT EXISTS (SELECT 1 FROM {target} t WHERE {join})").format(
            target=target, join=join)
        # I21 (B4): a new row is recorded by its key only; its values are in the target row, and its first
        # update records them as "before".
        after = sql.SQL("'{}'::jsonb")
        cur.execute(history_insert + sql.SQL("SELECT {run}, 'INSERT', {key_json}, {after} {new_rows}").format(
            run=sql.Literal(run_id), key_json=key_json, after=after, new_rows=new_rows))
        inserted = cur.rowcount
        if inserted:
            cur.execute(sql.SQL("INSERT INTO {target} ({cols}) SELECT {scols} {new_rows}").format(
                target=target, cols=sql.SQL(", ").join(_ident(c) for c in plan.columns),
                scols=sql.SQL(", ").join(sql.SQL("s.{}").format(_ident(c)) for c in plan.columns),
                new_rows=new_rows))
            if cur.rowcount != inserted:
                raise RunError("APPLY_MISMATCH", "Inserted rows differ from the history rows")
    return inserted, updated, stale, deleted


def chunk_watermark(dwh, plan: Plan) -> tuple[Any, str] | None:
    """Highest change value of the staged chunk: (typed value for comparison, text for the run)."""
    if plan.change_field is None:
        return None
    with dwh.cursor() as cur:
        column = _ident(plan.change_field["target_column"])
        cur.execute(sql.SQL("SELECT max({c}), max({c})::text FROM dwh_object_run_stage").format(c=column))
        value, text = cur.fetchone()
    return None if value is None else (value, text)


def staged_watermark(dwh, plan: Plan) -> str | None:
    """Highest change value of the staged rows (text), None without a change column or rows."""
    if plan.change_field is None:
        return None
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("SELECT max({})::text FROM dwh_object_run_stage").format(
            _ident(plan.change_field["target_column"])))
        return cur.fetchone()[0]


# ------------------------------------------------------------------ run


def _finish_args(ok: bool, error: RunError | None) -> tuple:
    """Error part of the finish call (package 014: column, key, bad_value, diagnostic_context)."""
    return (None if ok else error.code, None if ok else error.message[:2000],
            Jsonb(error.record_key) if (error and error.record_key) else None,
            error.column if error else None,
            error.bad_value if error else None,
            Jsonb(error.diagnostic) if (error and error.diagnostic) else None)


def _finish(dwh, run_id: int, ok: bool, result: RunResult, error: RunError | None = None) -> str:
    with dwh.cursor() as cur:
        cur.execute(
            "SELECT etl.object_run_finish(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, ok, result.rows_read, result.rows_inserted, result.rows_updated, result.rows_unchanged)
            + _finish_args(ok, error))
        return cur.fetchone()[0]


@dataclass
class ChunkTotals:
    """Rows of the chunks already committed by this run (I21, B5)."""
    chunk: int = 0
    read: int = 0
    inserted: int = 0
    updated: int = 0
    stale: int = 0
    deleted: int = 0
    watermark: tuple[Any, str] | None = None

    def add(self, read: int, inserted: int, updated: int, stale: int, deleted: int,
            mark: tuple[Any, str] | None = None) -> None:
        self.read += read
        self.inserted += inserted
        self.updated += updated
        self.stale += stale
        self.deleted += deleted
        if mark is not None and (self.watermark is None or mark[0] > self.watermark[0]):
            self.watermark = mark


def committed_rows(result, committed: ChunkTotals | None, error: RunError) -> None:
    """A failed run reports what its committed chunks wrote (B5); the message names the failed chunk."""
    done = committed if committed is not None else ChunkTotals()
    result.rows_read, result.rows_inserted, result.rows_updated = done.read, done.inserted, done.updated
    result.rows_unchanged = max(0, done.read - done.inserted - done.updated - done.stale)
    if hasattr(result, "rows_stale"):
        result.rows_stale, result.rows_deleted = done.stale, done.deleted
    if done.chunk > 1 or (done.chunk == 1 and done.read):
        error.message = (f"{error.message} [chunk {done.chunk}; {done.read} rows of the earlier chunks are written "
                         "and stay; run again to load the rest]")


def check_free_space(src, schema: str, table: str, log: Callable[[str], None]) -> None:
    """I21 (B3): the source size times 3 (STG, ODS, history) must fit on the disk of the DWH database, when both
    are known (DWH_PG_DATA_DIR, default /var/lib/postgresql); otherwise the check is skipped."""
    import shutil

    path = os.environ.get("DWH_PG_DATA_DIR", "").strip() or "/var/lib/postgresql"
    try:
        free = shutil.disk_usage(path).free
        with src.cursor() as cur:
            cur.execute("""SELECT pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n
                             ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'""",
                        (schema, table))
            row = cur.fetchone()
        src.rollback()
    except Exception:  # noqa: BLE001 - best effort: without both sizes the check is skipped
        try:
            src.rollback()
        except Exception:  # noqa: BLE001
            pass
        return
    if not row or not row[0]:
        return
    needed = int(row[0]) * 3
    log(f"STAGE: source size {int(row[0]) // 2**20} MB, free on the DWH disk {free // 2**20} MB")
    if needed > free:
        raise RunError("DISK_SPACE", f"Source object is about {int(row[0]) // 2**20} MB; loading needs about "
                       f"{needed // 2**20} MB, the DWH disk has {free // 2**20} MB free")


# I13-01 (R1 = A'): pages with at least this many records are checked for the fields of the mapping
API_DRIFT_MIN_RECORDS = 10


def api_chunks(doc: dict, secret: str | None, plan: Plan, watermark: str | None, log: Callable[[str], None],
               *, size: int | None = None, read_pages=None):
    """I11: rows of an API object (text values in plan.written order), in chunks of at most `size` rows.
    A mapped field that is absent in every record of the first non-empty page, or of any later page with at least
    API_DRIFT_MIN_RECORDS records, stops the run before that page is written (SOURCE_DRIFT; I13-01, decision R1 = A').
    Shorter pages are not judged: an API often omits empty fields, and a short last page may lack an optional one."""
    from dwh_api import api_request

    size = size or chunk_rows()
    reader = read_pages or api_request.read_pages
    names = [f["source_column"] for f in plan.written]
    chunk: list = []
    checked = False
    pages = 0
    for records in reader(doc, secret, watermark=watermark, overlap_minutes=plan.overlap_minutes, log=log):
        pages += 1
        flat = [api_request.flatten(r) for r in records]
        if flat and (not checked or len(flat) >= API_DRIFT_MIN_RECORDS):
            seen = set().union(*(f.keys() for f in flat))
            missing = [n for n in names if n not in seen]
            if missing:
                raise RunError("SOURCE_DRIFT", f"Fields of the mapping are not in page {pages} of the answer of the "
                               "API: " + ", ".join(missing[:20]))
            checked = True
        for f in flat:
            chunk.append([api_request.to_text(f.get(n)) for n in names])
            if len(chunk) >= size:
                yield chunk
                chunk = []
        if pages % 50 == 0:
            log(f"STAGE: {pages} pages read")
    log(f"STAGE: API answered {pages} page(s)")
    if chunk:
        yield chunk


def _record_file(dwh, run_id: int, entry, sha: str, actual_at, status: str, rows: int | None,
                 message: str | None) -> None:
    """I12: result for one file in the register (etl.object_file_record), committed at once."""
    with dwh.cursor() as cur:
        cur.execute("SELECT etl.object_file_record(%s, %s, %s, %s, %s, %s, %s, %s)",
                    (run_id, entry.name, entry.size, sha, actual_at, status, rows, message))
    dwh.commit()


def _warn_file(dwh, run_id: int, name: str, sha: str, code: str, message: str,
               log: Callable[[str], None]) -> None:
    """I12 (plan 7.7, decision A): a file refused while choosing is also a warning in the error journal
    (etl.object_file_warning, package 035): visible in "Ошибки и история", once per content and code; the run
    stays SUCCESS. Without package 035 the load goes on and only says so in its log."""
    try:
        with dwh.cursor() as cur:
            cur.execute("SELECT etl.object_file_warning(%s, %s, %s, %s, %s)", (run_id, name, sha, code, message))
        dwh.commit()
    except psycopg.errors.UndefinedFunction:
        dwh.rollback()
        log("STAGE: warning not written: package 035 (etl.object_file_warning) is not applied")


def file_records_chunks(records: list[dict], plan: Plan, *, size: int | None = None):
    """I12: rows of one file (text values in plan.written order). A mapped field absent in every record
    stops the file (SOURCE_DRIFT), as for an API answer."""
    from dwh_api import api_request

    size = size or chunk_rows()
    names = [f["source_column"] for f in plan.written]
    flat = [api_request.flatten(r) for r in records]
    if flat:
        seen = set().union(*(f.keys() for f in flat))
        missing = [n for n in names if n not in seen]
        if missing:
            raise RunError("SOURCE_DRIFT", "Fields of the mapping are not in the file: " + ", ".join(missing[:20]))
    for start in range(0, len(flat), size):
        yield [[api_request.to_text(f.get(n)) for n in names] for f in flat[start:start + size]]


def choose_files(dwh, run_id: int, cfg: dict, folder, log: Callable[[str], None], now: float) -> list[tuple]:
    """I12 (F6, F11): files to load, oldest first, as (entry, sha, actual_at). Every other new content gets
    its result in the register (NO_DATE, NOT_ACTUAL, FAILED); a file still being written is left for later."""
    from datetime import datetime

    from dwh_api import file_gateway as fg

    with dwh.cursor() as cur:
        cur.execute("SELECT etl.object_file_state(%s)", (run_id,))
        state = cur.fetchone()[0]
    dwh.commit()
    last = state.get("last_loaded_at")
    last_at = datetime.fromisoformat(last) if last else None
    known = state.get("known") or {}
    entries = fg.list_files(folder, cfg["mask"])
    log(f"STAGE: {len(entries)} file(s) match the mask {cfg['mask']}"
        + (f", last loaded file dated {last_at.isoformat()}" if last_at else ", nothing loaded yet"))
    actual: list[tuple] = []
    skipped = 0
    for entry in entries:
        try:
            body, sha = fg.read_file(entry)
        except fg.FileSourceError as exc:
            if exc.code == "FILE_TOO_LARGE":
                try:
                    digest = fg.hash_file(entry)
                except fg.FileSourceError:
                    digest = None
                if digest and known.get(digest) not in ("LOADED", "NOT_ACTUAL", "NO_DATE"):
                    _record_file(dwh, run_id, entry, digest, None, "FAILED", None, f"{exc.code}: {exc.message}")
                    _warn_file(dwh, run_id, entry.name, digest, exc.code, exc.message, log)
                log(f"STAGE: {entry.name}: {exc.code}")
            else:
                log(f"STAGE: {entry.name}: {exc.code}, left for the next run")
            continue
        status = known.get(sha)
        if status in ("LOADED", "NOT_ACTUAL", "NO_DATE"):
            skipped += 1
            continue
        try:
            parsed = fg.parse_file(body, cfg)
        except fg.FileSourceError as exc:
            if exc.code == "FILE_BROKEN" and now - entry.mtime < cfg["wait_seconds"]:
                log(f"STAGE: {entry.name}: not readable yet, changed {int(now - entry.mtime)} s ago: waiting")
                continue
            _record_file(dwh, run_id, entry, sha, None, "FAILED", None, f"{exc.code}: {exc.message}")
            _warn_file(dwh, run_id, entry.name, sha, exc.code, exc.message, log)
            log(f"STAGE: {entry.name}: {exc.code}")
            continue
        finally:
            del body
        if parsed.actual_at is None:
            reason = (f"No actuality date at '{cfg['date_path']}'"
                      + (f" (value '{parsed.date_text[:40]}')" if parsed.date_text else ""))
            _record_file(dwh, run_id, entry, sha, None, "NO_DATE", None, reason)
            _warn_file(dwh, run_id, entry.name, sha, "FILE_NO_DATE", reason, log)
            log(f"STAGE: {entry.name}: no actuality date, not loaded")
            continue
        if last_at is not None and parsed.actual_at <= last_at:
            _record_file(dwh, run_id, entry, sha, parsed.actual_at, "NOT_ACTUAL", None,
                         "Not newer than the last loaded file")
            log(f"STAGE: {entry.name}: dated {parsed.actual_at.isoformat()}, not newer than the last loaded file")
            continue
        actual.append((entry, sha, parsed.actual_at))
    if skipped:
        log(f"STAGE: {skipped} file(s) already in the register")
    actual.sort(key=lambda item: (item[2], item[0].name))
    chosen: list[tuple] = []
    for item in actual:
        if chosen and item[2] <= chosen[-1][2]:
            _record_file(dwh, run_id, item[0], item[1], item[2], "NOT_ACTUAL", None,
                         "Same date as another file of this run")
            continue
        chosen.append(item)
    if cfg["mode"] == "LATEST" and len(chosen) > 1:
        for entry, sha, actual_at in chosen[:-1]:
            _record_file(dwh, run_id, entry, sha, actual_at, "NOT_ACTUAL", None, "A newer file was loaded in this run")
        chosen = chosen[-1:]
    return chosen


def load_files(dwh, run_id: int, cfg: dict, code: str, plan: Plan, apply_chunks: Callable, log: Callable[[str], None],
               *, root=None, now: float | None = None) -> int:
    """I12: loads the chosen files one by one; each loaded file is LOADED in the register with its date.
    A file that fails is FAILED in the register and stops the run (the rows of earlier files stay)."""
    import time

    from dwh_api import file_gateway as fg

    folder = fg.source_folder(code, root)
    log(f"STAGE: files {cfg['format']} in {folder}, mode {cfg['mode']}")
    chosen = choose_files(dwh, run_id, cfg, folder, log, time.time() if now is None else now)
    if not chosen:
        log("STAGE: no new actual file")
    for entry, sha, actual_at in chosen:
        try:
            body, again = fg.read_file(entry)
            if again != sha:
                log(f"STAGE: {entry.name}: changed while the run read it, left for the next run")
                continue
            parsed = fg.parse_file(body, cfg)
            del body
            log(f"STAGE: {entry.name}: dated {actual_at.isoformat()}, {len(parsed.records)} record(s)")
            apply_chunks(file_records_chunks(parsed.records, plan))
        except (RunError, fg.FileSourceError, psycopg.Error) as caught:
            dwh.rollback()
            error = (caught if isinstance(caught, RunError) else RunError(caught.code, caught.message)
                     if isinstance(caught, fg.FileSourceError) else database_error(caught, "apply"))
            _record_file(dwh, run_id, entry, sha, actual_at, "FAILED", None,
                         f"{error.code}: {error.message}"[:2000])
            raise RunError(error.code, f"{entry.name}: {error.message}", record_key=error.record_key,
                           column=error.column, bad_value=error.bad_value, diagnostic=error.diagnostic) from None
        _record_file(dwh, run_id, entry, sha, actual_at, "LOADED", len(parsed.records), None)
    return len(chosen)


def run_object(source_object_id: int, *, dwh_connect: Callable[[], Any], read_credential: Callable[[str], str],
               source_connect_fn: Callable[[dict, str], Any] = source_connect, row_limit: int = ROW_LIMIT,
               log: Callable[[str], None] = lambda message: None, force_full: bool = False,
               read_object_credential: Callable[[int], str] | None = None, read_api_pages=None,
               file_root=None) -> RunResult:
    """force_full (I08, "Full rerun"): read the whole object once even when the version is INCREMENTAL;
    the watermark of the version is not used, the run records the new one as usual."""
    dwh = dwh_connect()
    try:
        try:
            with dwh.cursor() as cur:
                cur.execute("SELECT etl.object_run_begin(%s)", (source_object_id,))
                begin = cur.fetchone()[0]
            dwh.commit()
        except psycopg.Error as exc:
            dwh.rollback()
            message = (getattr(getattr(exc, "diag", None), "message_primary", None) or "refused").strip()
            raise RunError("RUN_REFUSED", message) from None
        result = RunResult(run_id=begin["run_id"], batch_run_id=begin["batch_run_id"], status="RUNNING")
        log(f"STAGE: run {result.run_id} started (batch_run {result.batch_run_id}, version {begin['version_no']})")
        source = begin["source"]
        definition = begin["definition"]
        try:
            plan = Plan(definition["target_schema"], definition["target_table"], definition["fields"], definition["key"],
                        definition.get("strategy"))
            result.load_strategy = plan.load_strategy
            result.watermark_from = (begin.get("watermark") if plan.load_strategy == "INCREMENTAL" and not force_full
                                     else None)
            where = increment_filter(plan, result.watermark_from)
            log(f"STAGE: strategy {plan.load_strategy}" + (
                f", watermark {result.watermark_from}, overlap {plan.overlap_minutes} min" if where is not None
                else (", full read requested" if force_full and plan.load_strategy == "INCREMENTAL"
                      else (", first run: full read" if plan.load_strategy == "INCREMENTAL" else ""))))
            check_target(dwh, plan)
            committed = ChunkTotals()

            def apply_chunks(chunks) -> None:
                for number, rows in enumerate(chunks, 1):
                    committed.chunk = number
                    try:
                        staged = stage_rows(dwh, plan, rows, log)
                    except psycopg.Error as exc:
                        raise database_error(exc, "stage") from None
                    validate_key(dwh, plan)
                    try:
                        inserted, updated, stale, deleted = apply_rows(dwh, plan, result.run_id)
                        mark = chunk_watermark(dwh, plan)
                    except psycopg.Error as exc:
                        raise database_error(exc, "apply") from None
                    dwh.commit()  # I21 (B1, B5): the chunk is written; a later failure keeps it
                    committed.add(staged, inserted, updated, stale, deleted, mark)
                    log(f"STAGE: chunk {number}: {staged} rows, {inserted} inserted, {updated} updated; "
                        f"total {committed.read} rows")

            if source.get("object_type") == "FILE":
                from dwh_api import file_gateway

                try:
                    cfg = file_gateway.normalize(source.get("file_config") or {})
                    load_files(dwh, result.run_id, cfg, source["code"], plan, apply_chunks, log, root=file_root)
                except file_gateway.FileSourceError as exc:
                    raise RunError(exc.code, exc.message) from None
            elif source.get("object_type") == "API":
                from dwh_api import api_request

                try:
                    doc = api_request.normalize(source.get("api_request") or {})
                except api_request.ApiRequestError as exc:
                    raise RunError(exc.code, exc.message) from None
                secret = None
                if doc["auth"]["kind"] != "NONE":
                    try:
                        if read_object_credential is None:
                            raise RuntimeError("no reader")
                        secret = read_object_credential(int(source["source_object_id"]))
                    except Exception as exc:
                        reason = str(exc) if type(exc).__name__ == "SourceSecretError" else ""
                        raise RunError("CREDENTIAL_UNAVAILABLE", "Credential of the API object could not be read "
                                       "from SecretStore" + (f" ({reason})" if reason else "")) from None
                if plan.load_strategy == "INCREMENTAL" and result.watermark_from and not (
                        doc["increment"] or any("{{watermark}}" in p["value"] for p in doc["params"])):
                    log("STAGE: the request has no increment parameter: the whole answer is read")
                log(f"STAGE: API request {doc['format']}, pagination {doc['pagination']['kind']}")
                try:
                    apply_chunks(api_chunks(doc, secret, plan, result.watermark_from, log, read_pages=read_api_pages))
                except api_request.ApiRequestError as exc:
                    raise RunError(exc.code, exc.message) from None
                finally:
                    secret = None
            else:
                try:
                    password = read_credential(source["code"])
                except Exception as exc:
                    # the reason of a SecretStore refusal (code of the database, no value) is shown; anything else is not
                    reason = str(exc) if type(exc).__name__ == "SourceSecretError" else ""
                    raise RunError("CREDENTIAL_UNAVAILABLE", "Source credential could not be read from SecretStore"
                                   + (f" ({reason})" if reason else "")) from None
                src = source_connect_fn(source["connection_config"], password)
                del password
                log("STAGE: source connected (read-only)")
                try:
                    check_structure(plan, source_columns(src, source["source_schema"], source["source_object"]))
                    estimate = estimated_rows(src, source["source_schema"], source["source_object"])
                    log(f"STAGE: source structure OK, estimated rows {estimate if estimate is not None else 'unknown'}")
                    check_free_space(src, source["source_schema"], source["source_object"], log)
                    apply_chunks(source_chunks(src, source["source_schema"], source["source_object"], plan, where, log))
                finally:
                    src.close()
            result.rows_read, result.rows_inserted, result.rows_updated = committed.read, committed.inserted, committed.updated
            result.rows_stale, result.rows_deleted = committed.stale, committed.deleted
            log(f"STAGE: {result.rows_read} rows read and applied in {committed.chunk} chunk(s)")
            try:
                result.watermark_to = committed.watermark[1] if committed.watermark else result.watermark_from
                with dwh.cursor() as cur:
                    cur.execute("SELECT etl.object_run_record_increment(%s, %s, %s, %s, %s)",
                                (result.run_id, result.watermark_from, result.watermark_to,
                                 result.rows_stale, result.rows_deleted))
            except psycopg.Error as exc:
                raise database_error(exc, "apply") from None
            log("STAGE: applied, committing")
            result.rows_unchanged = result.rows_read - result.rows_inserted - result.rows_updated - result.rows_stale
            result.status = _finish(dwh, result.run_id, True, result)
            dwh.commit()
            return result
        except (RunError, psycopg.Error, SourceGatewayError) as caught:
            error = (caught if isinstance(caught, RunError)
                     else RunError(caught.code, caught.message) if isinstance(caught, SourceGatewayError)
                     else database_error(caught, "dwh"))
            dwh.rollback()
            committed_rows(result, locals().get("committed"), error)
            result.status = _finish(dwh, result.run_id, False, result, error)
            dwh.commit()
            result.error_code, result.message = error.code, error.message
            return result
        except Exception as exc:  # unexpected: close the run, re-raise without data
            dwh.rollback()
            error = RunError("INTERNAL_ERROR", f"Unexpected {type(exc).__name__}")
            committed_rows(result, locals().get("committed"), error)
            result.status = _finish(dwh, result.run_id, False, result, error)
            dwh.commit()
            result.error_code, result.message = error.code, error.message
            return result
    finally:
        dwh.close()


# ------------------------------------------------------------------ ODS step (I06-05)


@dataclass
class OdsResult:
    ods_run_id: int
    batch_run_id: int
    status: str
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    error_code: str | None = None
    message: str | None = None


ODS_NOT_CONFIGURED = "has no published ODS configuration"
_OPERAND = {"TRIM": "btrim", "UPPER": "upper", "LOWER": "lower"}


def ods_plan(definition: dict) -> Plan:
    """The ODS version as a Plan of the apply step: every field is written (constants and technical
    fields have no STG column, they are marked as written by their transformation)."""
    fields = [{"target_column": f["target_column"], "source_column": f.get("stg_column") or f["transform"],
               "source_type": f.get("stg_type") or f["target_type"], "target_type": f["target_type"],
               "write_policy": f["write_policy"], "allow_explicit_null": f.get("allow_explicit_null", False)}
              for f in definition.get("fields") or []]
    return Plan("ods", definition["target_table"], fields, definition.get("key") or [])


def ods_expression(f: dict, ods_run_id: int) -> sql.Composable:
    """SQL of one ODS field from the STG row "s" (white list only; names quoted, values as literals)."""
    from dwh_api.field_types import postgres_type

    target = sql.SQL(postgres_type(f["target_type"]))
    transform = f["transform"]
    column = sql.SQL("s.{}").format(_ident(f["stg_column"])) if f.get("stg_column") else None
    if transform in ("COPY", "CAST"):
        value = column
    elif transform in _OPERAND:
        value = sql.SQL("{}(CAST({} AS text))").format(sql.SQL(_OPERAND[transform]), column)
    elif transform == "EMPTY_TO_NULL":
        value = sql.SQL("nullif(btrim(CAST({} AS text)), '')").format(column)
    elif transform == "DEFAULT":
        return sql.SQL("coalesce(CAST({} AS {t}), CAST({} AS {t}))").format(column, sql.Literal(f["param"]), t=target)
    elif transform == "CONSTANT":
        value = sql.Literal(f["param"])
    elif transform == "DATE_FROM_TEXT":
        value = sql.SQL("to_date(nullif(btrim(CAST({} AS text)), ''), {})").format(column, sql.Literal(f["param"]))
    elif transform == "LOAD_RUN_ID":
        value = sql.Literal(ods_run_id)
    elif transform == "LOADED_AT":
        value = sql.SQL("now()")
    else:
        raise RunError("CONFIG_INVALID", "Transformation is not in the white list")
    return sql.SQL("CAST({} AS {})").format(value, target)


def stg_primary_key(dwh, stg_table: str) -> list[str]:
    """Columns of the primary key of the STG table, in key order ([] without a primary key)."""
    with dwh.cursor() as cur:
        cur.execute("""SELECT array_agg(a.attname::text ORDER BY k.n)
                         FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid
                         JOIN pg_namespace ns ON ns.oid = t.relnamespace
                         CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY k(attnum, n)
                         JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                        WHERE ns.nspname = 'stg' AND t.relname = %s AND i.indisprimary""", (stg_table,))
        row = cur.fetchone()
    return list(row[0]) if row and row[0] else []


def ods_ranges(dwh, stg_table: str, size: int):
    """I21: conditions on the STG primary key that cut the STG table into chunks of `size` rows, in key order
    (each a SQL condition on the STG row "s"); one condition TRUE without a primary key."""
    key = stg_primary_key(dwh, stg_table)
    if not key:
        yield sql.SQL("TRUE")
        return
    row = sql.SQL("ROW({})").format(sql.SQL(", ").join(sql.SQL("s.{}").format(_ident(k)) for k in key))
    columns = sql.SQL(", ").join(_ident(k) for k in key)
    source = sql.SQL("stg.{}").format(_ident(stg_table))
    last = None
    while True:
        after = (sql.SQL("ROW({}) > ROW({})").format(columns, sql.SQL(", ").join(sql.Literal(v) for v in last))
                 if last is not None else sql.SQL("TRUE"))
        with dwh.cursor() as cur:
            cur.execute(sql.SQL("SELECT {} FROM {} WHERE {} ORDER BY {} OFFSET {} LIMIT 1").format(
                columns, source, after, columns, sql.Literal(size - 1)))
            upper = cur.fetchone()
        lower = (sql.SQL("{} > ROW({})").format(row, sql.SQL(", ").join(sql.Literal(v) for v in last))
                 if last is not None else sql.SQL("TRUE"))
        if upper is None:
            yield lower
            return
        yield sql.SQL("{} AND {} <= ROW({})").format(lower, row, sql.SQL(", ").join(sql.Literal(v) for v in upper))
        last = list(upper)


def stage_ods(dwh, plan: Plan, definition: dict, stg_table: str, ods_run_id: int, row_limit: int | None = None,
              condition: sql.Composable | None = None) -> int:
    """STG rows (of one chunk: `condition` on the STG row "s") -> temporary table with the ODS types."""
    from dwh_api.field_types import postgres_type

    fields = definition["fields"]
    source = sql.SQL("stg.{}").format(_ident(stg_table))
    where = condition if condition is not None else sql.SQL("TRUE")
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("CREATE TEMP TABLE dwh_object_run_stage ({}) ON COMMIT DROP").format(
            sql.SQL(", ").join(sql.SQL("{} {}").format(_ident(f["target_column"]), sql.SQL(postgres_type(f["target_type"])))
                               for f in fields)))
        cur.execute(sql.SQL("INSERT INTO dwh_object_run_stage ({}) SELECT {} FROM {} s WHERE {}").format(
            sql.SQL(", ").join(_ident(f["target_column"]) for f in fields),
            sql.SQL(", ").join(ods_expression(f, ods_run_id) for f in fields), source, where))
        return cur.rowcount


def _ods_finish(dwh, result: OdsResult, ok: bool, error: RunError | None = None) -> str:
    with dwh.cursor() as cur:
        cur.execute(
            "SELECT etl.object_ods_run_finish(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (result.ods_run_id, ok, result.rows_read, result.rows_inserted, result.rows_updated, result.rows_unchanged)
            + _finish_args(ok, error))
        return cur.fetchone()[0]


def _ods_rule(f: dict) -> str:
    from dwh_api.field_types import postgres_type

    rule = f["transform"]
    if f["transform"] == "DATE_FROM_TEXT" and f.get("param"):
        rule += " " + f["param"]
    return f"{rule} -> {postgres_type(f['target_type'])}"


def diagnose_ods(dwh, definition: dict, stg_table: str, ods_run_id: int, total: int) -> RunError | None:
    """After a data error of the ODS staging (the transaction is already rolled back): each field
    is computed alone by a temporary function of the session that catches the data error of its
    white list expression. Runs only after a failure; created objects vanish with the rollback."""
    fields = definition["fields"]
    by_target = {f["target_column"]: f for f in fields}
    key_cols = [by_target[k]["stg_column"] for k in definition.get("key") or []
                if k in by_target and by_target[k].get("stg_column")]
    if not key_cols:
        return None
    source = sql.SQL("stg.{}").format(_ident(stg_table))
    relation = sql.SQL("{} s").format(source)
    key_json = _jsonb_object(list(sql.SQL("{}, s.{}").format(sql.Literal(k), _ident(k)) for k in key_cols))
    key_order = sql.SQL(", ").join(sql.SQL("s.{}").format(_ident(k)) for k in key_cols)
    failed = []
    with dwh.cursor() as cur:
        checks = []
        for i, f in enumerate(fields):
            if f["transform"] in ("LOAD_RUN_ID", "LOADED_AT"):
                continue
            name = sql.Identifier(f"dwh_ods_ok_{i}")
            body = sql.SQL("BEGIN PERFORM {}; RETURN true; EXCEPTION WHEN data_exception THEN RETURN false; END").format(
                ods_expression(f, ods_run_id)).as_string(dwh)
            cur.execute(sql.SQL("CREATE FUNCTION pg_temp.{n}(s {src}) RETURNS boolean LANGUAGE plpgsql AS {b}").format(
                n=name, src=source, b=sql.Literal(body)))
            checks.append((f, sql.SQL("NOT pg_temp.{}(s)").format(name)))
        if not checks:
            return None
        cur.execute(sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(sql.SQL("count(*) FILTER (WHERE {})").format(c) for _, c in checks), relation))
        counts = cur.fetchone()
        for (f, invalid), n in zip(checks, counts):
            if not n:
                continue
            value = sql.SQL("CAST(s.{} AS text)").format(_ident(f["stg_column"])) if f.get("stg_column") else None
            values, more, keys = _report_field(cur, relation, invalid, value, key_json, key_order)
            failed.append({"column": f["target_column"], "rule": _ods_rule(f), "invalid_rows": n,
                           "values": values, "more_values": more, "keys": keys})
    return conversion_error("ODS", total, failed) if failed else None


def run_ods(source_object_id: int, stg_run_id: int | None, *, dwh_connect: Callable[[], Any],
            row_limit: int = ROW_LIMIT, log: Callable[[str], None] = lambda message: None) -> OdsResult | None:
    """ODS step of the object; None when the object has no published ODS version."""
    dwh = dwh_connect()
    try:
        try:
            with dwh.cursor() as cur:
                cur.execute("SELECT etl.object_ods_run_begin(%s, %s)", (source_object_id, stg_run_id))
                begin = cur.fetchone()[0]
            dwh.commit()
        except psycopg.Error as exc:
            dwh.rollback()
            message = (getattr(getattr(exc, "diag", None), "message_primary", None) or "refused").strip()
            if ODS_NOT_CONFIGURED in message:
                return None
            raise RunError("ODS_REFUSED", message) from None
        result = OdsResult(ods_run_id=begin["ods_run_id"], batch_run_id=begin["batch_run_id"], status="RUNNING")
        definition = begin["definition"]
        stg_table = begin["stg"]["table"]
        log(f"STAGE: ODS run {result.ods_run_id} started (batch_run {result.batch_run_id}, ODS version "
            f"{begin['version_no']}, stg.{stg_table} -> ods.{definition['target_table']})")
        try:
            plan = ods_plan(definition)
            check_target(dwh, plan)  # every ODS column exists with the published type
            committed = ChunkTotals()
            for number, condition in enumerate(ods_ranges(dwh, stg_table, chunk_rows()), 1):
                committed.chunk = number
                try:
                    staged = stage_ods(dwh, plan, definition, stg_table, result.ods_run_id, condition=condition)
                except psycopg.Error as exc:
                    error = database_error(exc, "ods")
                    state = getattr(getattr(exc, "diag", None), "sqlstate", None) or ""
                    if state.startswith("22"):  # data exception: report it field by field (I13-08)
                        dwh.rollback()
                        log("STAGE: ODS data error, checking each field")
                        with dwh.cursor() as cur:
                            cur.execute(sql.SQL("SELECT count(*) FROM stg.{}").format(_ident(stg_table)))
                            total = cur.fetchone()[0]
                        try:
                            error = diagnose_ods(dwh, definition, stg_table, result.ods_run_id, total) or error
                        except psycopg.Error:  # the report is optional: keep the original coded error
                            log("STAGE: ODS field check failed, original error kept")
                        dwh.rollback()
                    raise error from None
                validate_key(dwh, plan)
                try:
                    inserted, updated, _, _ = apply_rows(
                        dwh, plan, result.ods_run_id, history=("object_ods_record_change", "ods_run_id"))
                except psycopg.Error as exc:
                    raise database_error(exc, "apply") from None
                dwh.commit()  # I21: the chunk is written; a later failure keeps it
                committed.add(staged, inserted, updated, 0, 0)
                log(f"STAGE: ODS chunk {number}: {staged} rows, {inserted} inserted, {updated} updated; "
                    f"total {committed.read} rows")
            result.rows_read, result.rows_inserted, result.rows_updated = committed.read, committed.inserted, committed.updated
            log(f"STAGE: ODS {result.rows_read} STG rows transformed and applied in {committed.chunk} chunk(s)")
            result.rows_unchanged = result.rows_read - result.rows_inserted - result.rows_updated
            result.status = _ods_finish(dwh, result, True)
            dwh.commit()
            enqueue_marts(dwh, definition["target_table"], log)
            return result
        except (RunError, psycopg.Error) as caught:
            error = caught if isinstance(caught, RunError) else database_error(caught, "dwh")
            dwh.rollback()
            committed_rows(result, locals().get("committed"), error)
            result.status = _ods_finish(dwh, result, False, error)
            dwh.commit()
            result.error_code, result.message = error.code, error.message
            return result
        except Exception as exc:  # unexpected: close the run, without data
            dwh.rollback()
            error = RunError("INTERNAL_ERROR", f"Unexpected {type(exc).__name__}")
            committed_rows(result, locals().get("committed"), error)
            result.status = _ods_finish(dwh, result, False, error)
            dwh.commit()
            result.error_code, result.message = error.code, error.message
            return result
    finally:
        dwh.close()


def enqueue_marts(dwh, ods_table: str, log: Callable[[str], None]) -> int:
    """I19 (D8): after a successful ODS step, queue the published marts that read this ODS table.

    The ODS result is already committed; a failure here is only logged (before package 018 the function does
    not exist) and never changes the result of the run."""
    try:
        with dwh.cursor() as cur:
            cur.execute("SELECT etl.dm_mart_enqueue_after_ods(%s)", (ods_table,))
            queued = int(cur.fetchone()[0])
        dwh.commit()
    except psycopg.Error as exc:
        dwh.rollback()
        message = (getattr(getattr(exc, "diag", None), "message_primary", None) or type(exc).__name__).strip()
        log(f"WARNING: DM marts were not queued after ODS: {message}")
        return 0
    if queued:
        log(f"DM_QUEUED: {queued} mart(s) after ODS {ods_table}")
    return queued


def _print_ods(ods: OdsResult | None) -> bool:
    if ods is None:
        print("ODS_RESULT: NOT_CONFIGURED (no published ODS version)")
        return True
    print(f"ODS_RUN_ID: {ods.ods_run_id} | BATCH_RUN_ID: {ods.batch_run_id}")
    print(f"ODS_ROWS: read={ods.rows_read} inserted={ods.rows_inserted} updated={ods.rows_updated} "
          f"unchanged={ods.rows_unchanged}")
    if ods.status == "SUCCESS":
        print("ODS_RESULT: SUCCESS")
        return True
    print(f"ODS_RESULT: FAILED | code={ods.error_code} | {ods.message}")
    return False


def main() -> None:
    value = os.environ.get("DWH_RUN_OBJECT_ID", "").strip()
    if not value.isdigit():
        print("STOP: DWH_RUN_OBJECT_ID (source object id) is required")
        sys.exit(2)
    from dwh.config import get_dwh_config
    from dwh.db import connect
    from dwh.source_secret import read_object_credential, read_source_credential

    stage = os.environ.get("DWH_RUN_OBJECT_STAGE", "").strip().upper() or "ALL"
    if stage not in ("ALL", "ODS"):
        print("STOP: DWH_RUN_OBJECT_STAGE must be ALL or ODS")
        sys.exit(2)
    # I08: "Full rerun" of a request reads the whole object once (DWH_RUN_OBJECT_FULL=1).
    force_full = os.environ.get("DWH_RUN_OBJECT_FULL", "").strip() == "1"
    # I08: a run that takes longer than this prints the stack of every thread (repeatedly), so a hang
    # shows where it waits. Default 600 s; 0 switches it off.
    dump_after = int(os.environ.get("DWH_RUN_OBJECT_STACK_DUMP_SECONDS", "600") or "0")
    if dump_after > 0:
        import faulthandler

        faulthandler.dump_traceback_later(dump_after, repeat=True, file=sys.stdout)
    log = lambda m: print(m, flush=True)  # noqa: E731
    if stage == "ODS":
        try:
            ods = run_ods(int(value), None, dwh_connect=lambda: connect(get_dwh_config()), log=log)
        except RunError as error:
            print(f"ODS_RESULT: REFUSED | code={error.code} | {error.message}")
            sys.exit(1)
        sys.exit(0 if _print_ods(ods) and ods is not None else 1)
    try:
        result = run_object(int(value), dwh_connect=lambda: connect(get_dwh_config()),
                            read_credential=read_source_credential, log=log, force_full=force_full,
                            read_object_credential=read_object_credential)
    except RunError as error:
        print(f"RUN_RESULT: REFUSED | code={error.code} | {error.message}")
        sys.exit(1)
    print(f"RUN_ID: {result.run_id} | BATCH_RUN_ID: {result.batch_run_id}")
    print(f"ROWS: read={result.rows_read} inserted={result.rows_inserted} updated={result.rows_updated} "
          f"unchanged={result.rows_unchanged} stale={result.rows_stale} deleted={result.rows_deleted}")
    print(f"STRATEGY: {result.load_strategy}" + (f" | watermark {result.watermark_from} -> {result.watermark_to}"
                                                 if result.load_strategy == "INCREMENTAL" else ""))
    if result.status != "SUCCESS":
        print(f"RUN_RESULT: FAILED | code={result.error_code} | {result.message}")
        sys.exit(1)
    print("RUN_RESULT: SUCCESS")
    try:
        ods = run_ods(int(value), result.run_id, dwh_connect=lambda: connect(get_dwh_config()), log=log)
    except RunError as error:
        print(f"ODS_RESULT: REFUSED | code={error.code} | {error.message}")
        sys.exit(1)
    if not _print_ods(ods):
        sys.exit(1)


if __name__ == "__main__":
    main()
