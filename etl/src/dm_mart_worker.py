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

Only the DWH database is read and written (no source system), so the run stays in the dispatcher process;
DWH_DM_STATEMENT_TIMEOUT_SECONDS (default 1800) limits each statement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from dwh_api import dm_sql  # backend/src is on PYTHONPATH (tools/run_etl.ps1, dispatcher service)

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


def build(conn, defn: dm_sql.Definition, *, timeout: int | None = None,
          log: Callable[[str], None] = lambda m: None) -> MartResult:
    """Checks and reloads one mart; commits the data on success, rolls back otherwise."""
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
            fill, fill_params = dm_sql.fill_sql(defn, types)
            cur.execute(f'DELETE FROM "dm".{dm_sql.qi(defn.mart_code)}')
            deleted = cur.rowcount
            cur.execute(fill, fill_params)
            inserted = cur.rowcount
        conn.commit()
        log(f"STAGE: DM dm.{defn.mart_code} reloaded: deleted={deleted} inserted={inserted}")
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
        conn.rollback()
        result = build(conn, defn, timeout=timeout, log=log)
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
