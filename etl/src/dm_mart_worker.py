"""Engine of the DM step: reloads a mart from ODS (I19 step 5; package 018, decisions D5-D8).

The dispatcher (run_request_dispatcher.main) calls process_one() when the CRM queue and the object queue are
empty. One request per call, on the dispatcher connection (role dwh_etl), which owns the session lock taken
by the claim:

  etl.dm_mart_run_request_claim(worker) -> oldest QUEUED request of a mart that is not running, with the
                                           published version (a lost dispatcher's request is closed FAILED /
                                           DISPATCHER_LOST by the database)
  etl.dm_mart_run_begin                 -> RUNNING, run_id
  checks: the ODS tables and columns of the version exist; no NULL grain key; GRAIN / SAME_KEY keys and
          LOOKUP columns are unique (a duplicate would multiply rows) -> error with up to 10 examples
  one transaction: DELETE FROM dm.<code>; INSERT INTO dm.<code> SELECT ... (D6: readers see the old rows
          until the commit; TRUNCATE is not used because it blocks readers)
  etl.dm_mart_run_finish                -> SUCCESS / FAILED with rows, code, message and diagnostic
A PUBLISH request (package 019: a new structure published from the UI) fills dm.<code>__next instead; the
finish swaps it in and publishes the version in one transaction, or drops it when the run failed.

Only the DWH database is read and written (no source system), so the run stays in the dispatcher process;
DWH_DM_STATEMENT_TIMEOUT_SECONDS (default 1800) limits each statement.

A SQL version (I23, package 023) is filled by build_sql(): the stored query is read on a separate connection of
dwh_dm_sql_ro (SELECT on ods only, read-only, one REPEATABLE READ snapshot for the checks and the rows), the
key must be filled and unique, and the rows go into dm.<code> / dm.<code>__next by COPY under dwh_etl in the
transaction of the DELETE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from dwh_api import dm_sql  # backend/src is on PYTHONPATH (tools/run_etl.ps1, dispatcher service)
from dwh_api import sql_mart

DEFAULT_TIMEOUT_SECONDS = 1800


@dataclass
class MartResult:
    ok: bool
    rows_deleted: int | None = None
    rows_inserted: int | None = None
    error_code: str | None = None
    message: str | None = None
    diagnostic: dict[str, Any] | None = field(default=None)


def _primary(exc: psycopg.Error) -> str:
    diag = getattr(exc, "diag", None)
    return ((getattr(diag, "message_primary", None) or str(exc) or type(exc).__name__).strip())[:2000]


def _rows(cur) -> list[dict[str, Any]]:
    return dm_sql._rows(cur)


def build(conn, defn: dm_sql.Definition, *, timeout: int | None = None, target: str | None = None,
          log: Callable[[str], None] = lambda m: None) -> MartResult:
    """Checks and reloads one mart; commits the data on success, rolls back otherwise.

    target: the table to fill (default dm.<mart code>); a PUBLISH request (package 019) fills the table built
    aside, dm.<mart code>__next, which etl.dm_mart_run_finish swaps in."""
    table = target or defn.mart_code
    if timeout is None:
        timeout = int(os.environ.get("DWH_DM_STATEMENT_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
    try:
        with conn.cursor() as cur:
            types = dm_sql.load_column_types(cur, defn.tables())
            missing = dm_sql.missing_columns(defn, types)
            if missing:
                conn.rollback()
                return MartResult(False, error_code="DM_SOURCE_MISSING",
                                  message="ODS objects of the version are missing: " + ", ".join(missing[:20]),
                                  diagnostic={"missing": missing[:50]})
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (f"{int(timeout) * 1000}",))
            text, params = dm_sql.null_key_check(defn, types)
            cur.execute(text, params)
            nulls = cur.fetchone()
            nulls = int(nulls[0] if not isinstance(nulls, dict) else list(nulls.values())[0])
            if nulls:
                g = defn.grain
                conn.rollback()
                return MartResult(False, error_code="DM_KEY_NULL",
                                  message=f"{nulls} rows of ods.{g.table} (source {g.alias}) have an empty key {g.key_column}",
                                  diagnostic={"alias": g.alias, "table": g.table, "column": g.key_column, "rows": nulls})
            for check in dm_sql.key_checks(defn, types):
                cur.execute(check.sql, check.params)
                dups = _rows(cur)
                if dups:
                    examples = [{"key": r["key_value"], "rows": int(r["rows"])} for r in dups]
                    total = int(dups[0]["duplicate_keys"])
                    shown = ", ".join(f"{e['key']} x{e['rows']}" for e in examples)
                    conn.rollback()
                    return MartResult(False, error_code=check.code,
                                      message=(f"{total} values of {check.column} in ods.{check.table} (source "
                                               f"{check.alias}) occur more than once: {shown}"),
                                      diagnostic={"alias": check.alias, "table": check.table, "column": check.column,
                                                  "duplicate_keys": total, "examples": examples})
            log(f"STAGE: DM keys checked ({len(defn.sources)} sources)")
            fill, fill_params = dm_sql.fill_sql(defn, types, table)
            cur.execute(f'DELETE FROM "dm".{dm_sql.qi(table)}')
            deleted = cur.rowcount
            cur.execute(fill, fill_params)
            inserted = cur.rowcount
        conn.commit()
        log(f"STAGE: DM dm.{table} reloaded: deleted={deleted} inserted={inserted}")
        return MartResult(True, rows_deleted=deleted, rows_inserted=inserted)
    except dm_sql.DefinitionError as exc:
        conn.rollback()
        return MartResult(False, error_code="DM_DEFINITION_INVALID", message=str(exc))
    except psycopg.Error as exc:
        conn.rollback()
        state = getattr(getattr(exc, "diag", None), "sqlstate", None) or getattr(exc, "sqlstate", None) or ""
        code = ("DM_TIMEOUT" if state == "57014" else "DM_DATA_ERROR" if state[:2] in ("22", "23")
                else "DM_TABLE_MISMATCH" if state in ("42703", "42P01", "42804") else "DM_DATABASE_ERROR")
        return MartResult(False, error_code=code, message=_primary(exc), diagnostic={"sqlstate": state})


def _sql_error(exc: psycopg.Error) -> MartResult:
    state = getattr(getattr(exc, "diag", None), "sqlstate", None) or getattr(exc, "sqlstate", None) or ""
    code = ("DM_TIMEOUT" if state == "57014" else "DM_SQL_NOT_ALLOWED" if state in ("42501", "25006")
            else "DM_DATA_ERROR" if state[:2] in ("22", "23")
            else "DM_TABLE_MISMATCH" if state in ("42703", "42P01", "42804") else "DM_DATABASE_ERROR")
    return MartResult(False, error_code=code, message=_primary(exc), diagnostic={"sqlstate": state})


def build_sql(conn, defn: dm_sql.Definition, sql_text: str, *, timeout: int | None = None, target: str | None = None,
              log: Callable[[str], None] = lambda m: None, ro_connect: Callable[[int], Any] | None = None) -> MartResult:
    """Checks and reloads a mart of a SQL version (I23); commits the data on success, rolls back otherwise.

    ro_connect(timeout_ms) opens the dwh_dm_sql_ro connection (default: the DWH database of conn)."""
    table = target or defn.mart_code
    if timeout is None:
        timeout = int(os.environ.get("DWH_DM_STATEMENT_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
    keys = [f for f in defn.fields if f.is_key]
    if len(keys) != 1 or not sql_text:
        return MartResult(False, error_code="DM_DEFINITION_INVALID", message="a SQL version needs its query and one key field")
    key = keys[0].name
    fields = [(f.name, f.data_type) for f in defn.fields]
    try:
        if ro_connect is None:
            info = conn.info
            ro = sql_mart.connect(info.host, info.port, info.dbname, int(timeout) * 1000)
        else:
            ro = ro_connect(int(timeout) * 1000)
    except sql_mart.SqlMartError as exc:
        return MartResult(False, error_code=exc.code, message=str(exc))
    except psycopg.Error as exc:
        return MartResult(False, error_code="DM_SQL_CONNECT_FAILED", message=f"{sql_mart.ROLE}: {_primary(exc)}")
    try:
        ro.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with ro.cursor() as rc:
            rc.execute(sql_mart.wrapped(sql_text) + " LIMIT 0")
            names = [d.name for d in (rc.description or [])]
            missing = [name for name, _ in fields if name not in names]
            if missing:
                ro.rollback()
                return MartResult(False, error_code="DM_SQL_COLUMNS_CHANGED",
                                  message="the query no longer returns the columns of the mart: " + ", ".join(missing[:20]),
                                  diagnostic={"missing": missing[:50]})
            rc.execute(sql_mart.key_null_sql(sql_text, key))
            row = rc.fetchone()
            nulls = int(row["n"] if isinstance(row, dict) else row[0])
            if nulls:
                ro.rollback()
                return MartResult(False, error_code="DM_KEY_NULL",
                                  message=f"{nulls} rows of the query have an empty key {key}",
                                  diagnostic={"column": key, "rows": nulls})
            rc.execute(sql_mart.key_duplicates_sql(sql_text, key, dm_sql.KEY_EXAMPLES))
            dups = _rows(rc)
            if dups:
                examples = [{"key": r["key_value"], "rows": int(r["rows"])} for r in dups]
                total = int(dups[0]["duplicate_keys"])
                shown = ", ".join(f"{e['key']} x{e['rows']}" for e in examples)
                ro.rollback()
                return MartResult(False, error_code="DM_KEY_NOT_UNIQUE",
                                  message=f"{total} values of the key {key} occur more than once in the query: {shown}",
                                  diagnostic={"column": key, "duplicate_keys": total, "examples": examples})
        log(f"STAGE: DM SQL keys checked (key {key})")
        columns = ", ".join(sql_mart.qi(name) for name, _ in fields)
        with conn.cursor() as cur, ro.cursor() as rc:
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (f"{int(timeout) * 1000}",))
            cur.execute(f'DELETE FROM "dm".{dm_sql.qi(table)}')
            deleted = cur.rowcount
            with rc.copy(f"COPY ({sql_mart.read_sql(sql_text, fields)}) TO STDOUT") as src, \
                    cur.copy(f'COPY "dm".{dm_sql.qi(table)} ({columns}, "dm_loaded_at") FROM STDIN') as dst:
                for block in src:
                    dst.write(block)
            inserted = cur.rowcount
        conn.commit()
        ro.rollback()
        log(f"STAGE: DM dm.{table} reloaded from SQL: deleted={deleted} inserted={inserted}")
        return MartResult(True, rows_deleted=deleted, rows_inserted=inserted)
    except psycopg.Error as exc:
        conn.rollback()
        try:
            ro.rollback()
        except psycopg.Error:
            pass
        return _sql_error(exc)
    finally:
        ro.close()


def _finish(conn, run_id: int, result: MartResult) -> str:
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("SELECT etl.dm_mart_run_finish(%s, %s, %s, %s, %s, %s, %s)",
                    (run_id, result.ok, result.rows_deleted, result.rows_inserted,
                     None if result.ok else (result.error_code or "DM_FAILED"),
                     None if result.ok else (result.message or "")[:4000],
                     Jsonb(result.diagnostic) if result.diagnostic is not None else None))
        row = cur.fetchone()
    conn.commit()
    return row[0] if not isinstance(row, dict) else list(row.values())[0]


def process_one(conn, worker: str, *, log: Callable[[str], None] = lambda m: print(m, flush=True),
                timeout: int | None = None) -> bool:
    """Claims and executes one mart run request. False when nothing can be claimed."""
    with conn.cursor() as cur:
        cur.execute("SELECT request_id, mart_id, version_id, trigger_kind FROM etl.dm_mart_run_request_claim(%s)",
                    (worker,))
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return False
    if isinstance(row, dict):
        row = (row["request_id"], row["mart_id"], row["version_id"], row["trigger_kind"])
    request_id, mart_id, version_id, trigger = int(row[0]), int(row[1]), int(row[2]), str(row[3])
    with conn.cursor() as cur:
        cur.execute("SELECT etl.dm_mart_run_begin(%s, %s, %s)", (request_id, worker, version_id))
        run_id = int(cur.fetchone()[0])
    conn.commit()
    log(f"DM REQUEST CLAIMED {request_id} mart={mart_id} version={version_id} trigger={trigger} run={run_id}")
    try:
        with conn.cursor() as cur:
            defn = dm_sql.load_definition(cur, version_id)
            # to_jsonb: a database without package 023 has no version_kind (every version is a builder one).
            cur.execute("SELECT to_jsonb(v) ->> 'version_kind' AS version_kind, to_jsonb(v) ->> 'sql_text' AS sql_text "
                        "FROM meta.dm_mart_version v WHERE v.version_id = %s", (version_id,))
            kind_row = _rows(cur)
        conn.rollback()
        # PUBLISH (package 019): fill the table built aside; the finish swaps it in and publishes the version.
        target = f"{defn.mart_code}__next" if trigger == "PUBLISH" else None
        if kind_row and kind_row[0]["version_kind"] == "SQL":
            result = build_sql(conn, defn, kind_row[0]["sql_text"], timeout=timeout, target=target, log=log)
        else:
            result = build(conn, defn, timeout=timeout, target=target, log=log)
    except dm_sql.DefinitionError as exc:
        conn.rollback()
        result = MartResult(False, error_code="DM_DEFINITION_INVALID", message=str(exc))
    except psycopg.Error as exc:
        conn.rollback()
        result = MartResult(False, error_code="DM_DATABASE_ERROR", message=_primary(exc))
    except Exception as exc:  # unexpected: close the run without data
        conn.rollback()
        result = MartResult(False, error_code="INTERNAL_ERROR", message=f"Unexpected {type(exc).__name__}")
    status = _finish(conn, run_id, result)
    log(f"DM REQUEST {status} {request_id} mart={mart_id}"
        + (f" deleted={result.rows_deleted} inserted={result.rows_inserted}" if result.ok
           else f" code={result.error_code} | {result.message}"))
    return True
