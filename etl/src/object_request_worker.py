"""Execution of object run requests by the dispatcher (I08, package 015).

The dispatcher (run_request_dispatcher.main) calls process_one() when the CRM queue is empty. One request per
call, on the dispatcher connection (role dwh_etl), which owns the session lock taken by the claim:

  etl.object_run_request_claim(worker)  -> oldest QUEUED request, CLAIMED (a request of a lost dispatcher
                                           is closed FAILED / DISPATCHER_LOST by the database)
  etl.object_run_request_start          -> RUNNING
  RUN          run_object + run_ods (strategy of the version in force)
  FULL_RERUN   run_object reading the whole object once (force_full) + run_ods
  CONTINUE_ODS run_ods alone (the database allowed it only after a failed ODS step that follows a successful STG run)
  etl.object_run_request_finish         -> SUCCESS / FAILED with the STG and ODS runs, result code, message

The runs keep their own connections, journals and error reports (I06, I07, I06-05, I13-08); the request only links
them. Messages carry codes and counts; a conversion report (I13-08) is passed on as the engine wrote it.
"""

from __future__ import annotations

from typing import Any, Callable

import run_object as engine


def _finish(conn, request_id: int, ok: bool, stg_run_id, ods_run_id, code: str | None, message: str | None) -> str:
    conn.rollback()  # clean transaction state on the dispatcher connection before closing the request
    with conn.cursor() as cur:
        cur.execute("SELECT etl.object_run_request_finish(%s, %s, %s, %s, %s, %s)",
                    (request_id, ok, stg_run_id, ods_run_id, None if ok else (code or "RUN_FAILED"),
                     (message or "")[:4000] or None))
        status = cur.fetchone()[0]
    conn.commit()
    return status


def _stg_text(r: engine.RunResult) -> str:
    return (f"STG run {r.run_id}: read {r.rows_read}, inserted {r.rows_inserted}, updated {r.rows_updated}, "
            f"unchanged {r.rows_unchanged}")


def _ods_text(o: engine.OdsResult) -> str:
    return (f"ODS run {o.ods_run_id}: read {o.rows_read}, inserted {o.rows_inserted}, updated {o.rows_updated}, "
            f"unchanged {o.rows_unchanged}")


def execute(source_object_id: int, run_kind: str, *, dwh_connect: Callable[[], Any],
            read_credential: Callable[[str], str], log: Callable[[str], None]) -> tuple[bool, Any, Any, str | None, str]:
    """Runs the steps of one request. Returns (ok, stg_run_id, ods_run_id, result_code, message)."""
    stg_id = ods_id = None
    parts: list[str] = []
    try:
        if run_kind in ("RUN", "FULL_RERUN"):
            r = engine.run_object(source_object_id, dwh_connect=dwh_connect, read_credential=read_credential, log=log,
                                  force_full=run_kind == "FULL_RERUN")
            stg_id = r.run_id
            if r.status != "SUCCESS":
                return False, stg_id, None, r.error_code, f"STG run {r.run_id} FAILED: {r.message}"
            parts.append(_stg_text(r))
            ods = engine.run_ods(source_object_id, r.run_id, dwh_connect=dwh_connect, log=log)
        elif run_kind == "CONTINUE_ODS":
            ods = engine.run_ods(source_object_id, None, dwh_connect=dwh_connect, log=log)
            if ods is None:
                return False, None, None, "ODS_NOT_CONFIGURED", "Object has no published ODS configuration"
        else:
            return False, None, None, "UNSUPPORTED_RUN_KIND", f"Unsupported run kind {run_kind}"
        if ods is None:
            parts.append("no published ODS configuration")
            return True, stg_id, None, None, "; ".join(parts)
        ods_id = ods.ods_run_id
        if ods.status != "SUCCESS":
            parts.append(f"ODS run {ods.ods_run_id} FAILED: {ods.message}")
            return False, stg_id, ods_id, ods.error_code, "; ".join(parts)
        parts.append(_ods_text(ods))
        return True, stg_id, ods_id, None, "; ".join(parts)
    except engine.RunError as error:  # refused by begin (object state, lock, configuration)
        parts.append(error.message)
        return False, stg_id, ods_id, error.code, "; ".join(parts)
    except Exception as exc:  # unexpected: no data, only the type
        parts.append(f"Unexpected {type(exc).__name__}")
        return False, stg_id, ods_id, "INTERNAL_ERROR", "; ".join(parts)


def process_one(conn, worker: str, *, dwh_connect: Callable[[], Any] | None = None,
                read_credential: Callable[[str], str] | None = None,
                log: Callable[[str], None] = lambda m: print(m, flush=True)) -> bool:
    """Claims and executes one object run request. False when the queue is empty."""
    with conn.cursor() as cur:
        cur.execute("SELECT request_id, source_object_id, run_kind FROM etl.object_run_request_claim(%s)", (worker,))
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return False
    request_id, source_object_id, run_kind = int(row[0]), int(row[1]), str(row[2])
    log(f"OBJECT REQUEST CLAIMED {request_id} object={source_object_id} kind={run_kind}")
    with conn.cursor() as cur:
        cur.execute("SELECT etl.object_run_request_start(%s, %s)", (request_id, worker))
    conn.commit()
    if dwh_connect is None:
        from dwh.config import get_dwh_config
        from dwh.db import connect

        dwh_connect = lambda: connect(get_dwh_config())  # noqa: E731
    if read_credential is None:
        from dwh.source_secret import read_source_credential

        read_credential = read_source_credential
    ok, stg_id, ods_id, code, message = execute(source_object_id, run_kind, dwh_connect=dwh_connect,
                                                read_credential=read_credential, log=log)
    status = _finish(conn, request_id, ok, stg_id, ods_id, code, message)
    log(f"OBJECT REQUEST {status} {request_id}" + (f" code={code}" if not ok else "") + f" | {message}")
    return True
