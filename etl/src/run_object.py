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
                                     into a temporary table with the target types (explicit CAST
                                     by PostgreSQL; a failure stops the run), at most ROW_LIMIT
                                     rows (R6); progress is printed.
  5. validation                      key without NULL and without duplicates.
  6. apply (one transaction)         new key -> INSERT (AUTHORITATIVE and INSERT_ONLY
                                     fields); existing key -> UPDATE of AUTHORITATIVE fields
                                     that changed; NULL clears a value only with
                                     allow_explicit_null; equal rows are skipped; no deletes
                                     (R2). Every INSERT/UPDATE gets a history row (R5);
                                     etl.object_run_finish(SUCCESS) in the same transaction.
  Any error: rollback of all target writes, then the run is closed FAILED with a code.

Messages never contain credentials or source values; the business key of a failing
row may be recorded (etl.error_log.record_key), as in the CRM ETL.

Usage: set DWH_RUN_OBJECT_ID and run (tools\\run_etl.ps1 -Task run-object -ObjectId <id>).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from dwh_api.source_gateway import SourceGatewayError  # backend/src is on PYTHONPATH (tools/run_etl.ps1)

ROW_LIMIT = 1_000_000
FETCH_SIZE = 5_000
SOURCE_CONNECT_TIMEOUT = 15
SOURCE_STATEMENT_TIMEOUT_MS = 30 * 60 * 1000


class RunError(Exception):
    """A run failure with a stable code; the message never contains data or secrets."""

    def __init__(self, code: str, message: str, *, record_key: dict | None = None, column: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.record_key = record_key
        self.column = column


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
    message = f"{stage}: {code} (SQLSTATE {state or 'unknown'})" + (f", column {column}" if column else "")
    return RunError(code, message, column=column)


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
            statement_timeout_ms=SOURCE_STATEMENT_TIMEOUT_MS,
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


def read_source(conn, schema: str, table: str, plan: Plan, row_limit: int, where: sql.Composable | None = None):
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
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DECLARE dwh_object_run NO SCROLL CURSOR FOR {}").format(select))
            while True:
                cur.execute(sql.SQL("FETCH FORWARD {} FROM dwh_object_run").format(sql.Literal(FETCH_SIZE)))
                batch = cur.fetchall()
                if not batch:
                    break
                for row in batch:
                    count += 1
                    if count > row_limit:
                        raise RunError("ROW_LIMIT", f"Source object has more than {row_limit} rows")
                    yield row
    except psycopg.Error as exc:
        raise database_error(exc, "source") from None
    finally:
        conn.rollback()


# ------------------------------------------------------------------ DWH apply


def _ident(name: str) -> sql.Identifier:
    return sql.Identifier(name)


STAGE_BATCH = 1_000


def stage_rows(dwh, plan: Plan, rows, log: Callable[[str], None] = lambda m: None) -> int:
    """Temporary table with the target types; text values are converted by PostgreSQL (explicit CAST).

    Plain multi-row INSERTs in batches (no COPY: a COPY to the DWH interleaved with the source
    FETCHes hung on the user's Windows PC on 05.11.2026).
    """
    from dwh_api.field_types import postgres_type

    types = [postgres_type(f["target_type"]) for f in plan.written]
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("CREATE TEMP TABLE dwh_object_run_stage ({}) ON COMMIT DROP").format(
            sql.SQL(", ").join(sql.SQL("{} {}").format(_ident(f["target_column"]), sql.SQL(t))
                               for f, t in zip(plan.written, types))))
        row_sql = sql.SQL("({})").format(sql.SQL(", ").join(
            sql.SQL("CAST({} AS {})").format(sql.Placeholder(), sql.SQL(t)) for t in types))
        head = sql.SQL("INSERT INTO dwh_object_run_stage ({}) VALUES ").format(
            sql.SQL(", ").join(_ident(c) for c in plan.columns))
        batch: list = []
        count = 0

        def flush():
            if batch:
                cur.execute(head + sql.SQL(", ").join([row_sql] * len(batch)), [v for r in batch for v in r])
                batch.clear()

        for row in rows:
            batch.append(row)
            count += 1
            if len(batch) >= STAGE_BATCH:
                flush()
                if count % 50_000 == 0:
                    log(f"STAGE: {count} rows staged")
        flush()
    return count


def validate_key(dwh, plan: Plan) -> None:
    key = sql.SQL(", ").join(_ident(k) for k in plan.key)
    key_json = sql.SQL("jsonb_build_object({})").format(
        sql.SQL(", ").join(sql.SQL("{}, {}").format(sql.Literal(k), _ident(k)) for k in plan.key))
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM dwh_object_run_stage WHERE {}").format(
            sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(_ident(k)) for k in plan.key)))
        nulls = cur.fetchone()[0]
        if nulls:
            raise RunError("KEY_NULL", f"{nulls} source rows have an empty business key")
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


def apply_rows(dwh, plan: Plan, run_id: int) -> tuple[int, int, int, int]:
    """INSERT new keys, UPDATE changed AUTHORITATIVE fields, history rows.

    Returns (inserted, updated, stale, deleted). With a change column, a row whose change value
    in the source is older than in the DWH is skipped as stale (D4). With a delete flag, an update
    that sets the flag to true is recorded as DELETE (soft delete, D5)."""
    target = sql.SQL("{}.{}").format(_ident(plan.target_schema), _ident(plan.target_table))
    join = sql.SQL(" AND ").join(sql.SQL("t.{k} = s.{k}").format(k=_ident(k)) for k in plan.key)
    key_json = sql.SQL("jsonb_build_object({})").format(
        sql.SQL(", ").join(sql.SQL("{}, s.{}").format(sql.Literal(k), _ident(k)) for k in plan.key))
    updated = stale = deleted = 0
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
            cur.execute(sql.SQL(
                "INSERT INTO etl.object_record_change(run_id, change_kind, record_key, changes) "
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
        after = sql.SQL("jsonb_build_object({})").format(sql.SQL(", ").join(
            sql.SQL("{}, jsonb_build_object('after', to_jsonb(s.{}))").format(sql.Literal(c), _ident(c))
            for c in plan.columns))
        cur.execute(sql.SQL("INSERT INTO etl.object_record_change(run_id, change_kind, record_key, changes) "
                            "SELECT {run}, 'INSERT', {key_json}, {after} {new_rows}").format(
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


def staged_watermark(dwh, plan: Plan) -> str | None:
    """Highest change value of the staged rows (text), None without a change column or rows."""
    if plan.change_field is None:
        return None
    with dwh.cursor() as cur:
        cur.execute(sql.SQL("SELECT max({})::text FROM dwh_object_run_stage").format(
            _ident(plan.change_field["target_column"])))
        return cur.fetchone()[0]


# ------------------------------------------------------------------ run


def _finish(dwh, run_id: int, ok: bool, result: RunResult, error: RunError | None = None) -> str:
    with dwh.cursor() as cur:
        cur.execute(
            "SELECT etl.object_run_finish(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, ok, result.rows_read, result.rows_inserted, result.rows_updated, result.rows_unchanged,
             None if ok else error.code, None if ok else error.message[:2000],
             Jsonb(error.record_key) if (error and error.record_key) else None,
             error.column if error else None))
        return cur.fetchone()[0]


def run_object(source_object_id: int, *, dwh_connect: Callable[[], Any], read_credential: Callable[[str], str],
               source_connect_fn: Callable[[dict, str], Any] = source_connect, row_limit: int = ROW_LIMIT,
               log: Callable[[str], None] = lambda message: None) -> RunResult:
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
            result.watermark_from = begin.get("watermark") if plan.load_strategy == "INCREMENTAL" else None
            where = increment_filter(plan, result.watermark_from)
            log(f"STAGE: strategy {plan.load_strategy}" + (
                f", watermark {result.watermark_from}, overlap {plan.overlap_minutes} min" if where is not None
                else (", first run: full read" if plan.load_strategy == "INCREMENTAL" else "")))
            check_target(dwh, plan)
            try:
                password = read_credential(source["code"])
            except Exception:
                raise RunError("CREDENTIAL_UNAVAILABLE", "Source credential could not be read from SecretStore") from None
            src = source_connect_fn(source["connection_config"], password)
            del password
            log("STAGE: source connected (read-only)")
            try:
                check_structure(plan, source_columns(src, source["source_schema"], source["source_object"]))
                estimate = estimated_rows(src, source["source_schema"], source["source_object"])
                log(f"STAGE: source structure OK, estimated rows {estimate if estimate is not None else 'unknown'}")
                if where is None and estimate is not None and estimate > row_limit:
                    raise RunError("ROW_LIMIT", f"Source object has about {estimate} rows, limit {row_limit}")
                try:
                    result.rows_read = stage_rows(dwh, plan, read_source(
                        src, source["source_schema"], source["source_object"], plan, row_limit, where), log)
                except psycopg.Error as exc:
                    raise database_error(exc, "stage") from None
            finally:
                src.close()
            log(f"STAGE: {result.rows_read} rows read and converted")
            validate_key(dwh, plan)
            log("STAGE: business key OK")
            try:
                result.rows_inserted, result.rows_updated, result.rows_stale, result.rows_deleted = apply_rows(
                    dwh, plan, result.run_id)
                staged = staged_watermark(dwh, plan)
                result.watermark_to = staged if staged is not None else result.watermark_from
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
            result.rows_inserted = result.rows_updated = result.rows_unchanged = result.rows_stale = result.rows_deleted = 0
            result.status = _finish(dwh, result.run_id, False, result, error)
            dwh.commit()
            result.error_code, result.message = error.code, error.message
            return result
        except Exception as exc:  # unexpected: close the run, re-raise without data
            dwh.rollback()
            error = RunError("INTERNAL_ERROR", f"Unexpected {type(exc).__name__}")
            result.rows_inserted = result.rows_updated = result.rows_unchanged = result.rows_stale = result.rows_deleted = 0
            result.status = _finish(dwh, result.run_id, False, result, error)
            dwh.commit()
            result.error_code, result.message = error.code, error.message
            return result
    finally:
        dwh.close()


def main() -> None:
    value = os.environ.get("DWH_RUN_OBJECT_ID", "").strip()
    if not value.isdigit():
        print("STOP: DWH_RUN_OBJECT_ID (source object id) is required")
        sys.exit(2)
    from dwh.config import get_dwh_config
    from dwh.db import connect
    from dwh.source_secret import read_source_credential

    try:
        result = run_object(int(value), dwh_connect=lambda: connect(get_dwh_config()),
                            read_credential=read_source_credential, log=lambda m: print(m, flush=True))
    except RunError as error:
        print(f"RUN_RESULT: REFUSED | code={error.code} | {error.message}")
        sys.exit(1)
    print(f"RUN_ID: {result.run_id} | BATCH_RUN_ID: {result.batch_run_id}")
    print(f"ROWS: read={result.rows_read} inserted={result.rows_inserted} updated={result.rows_updated} "
          f"unchanged={result.rows_unchanged} stale={result.rows_stale} deleted={result.rows_deleted}")
    print(f"STRATEGY: {result.load_strategy}" + (f" | watermark {result.watermark_from} -> {result.watermark_to}"
                                                 if result.load_strategy == "INCREMENTAL" else ""))
    if result.status == "SUCCESS":
        print("RUN_RESULT: SUCCESS")
        return
    print(f"RUN_RESULT: FAILED | code={result.error_code} | {result.message}")
    sys.exit(1)


if __name__ == "__main__":
    main()
