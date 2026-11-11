"""Execution of object run requests by the dispatcher (I08, package 015).

The dispatcher (run_request_dispatcher.main) calls process_one() when the CRM queue is empty. One request per
call, on the dispatcher connection (role dwh_etl), which owns the session lock taken by the claim:

  etl.object_run_request_claim(worker)  -> oldest QUEUED request, CLAIMED (a request of a lost dispatcher
                                           is closed FAILED / DISPATCHER_LOST by the database)
  etl.object_run_request_start          -> RUNNING
  the run itself in a CHILD PROCESS: python run_object.py (the proven command of tools\run_etl.ps1 -Task run-object)
    RUN          STG + ODS (strategy of the version in force)
    FULL_RERUN   STG reading the whole object once (DWH_RUN_OBJECT_FULL=1) + ODS
    CONTINUE_ODS ODS alone (DWH_RUN_OBJECT_STAGE=ODS; the database allowed it only after a failed ODS step that
                 follows a successful STG run)
  etl.object_run_request_finish         -> SUCCESS / FAILED with the STG and ODS runs, result code, message

Why a child process: on 11.11.2026 a run started inside the API/dispatcher process waited forever on the network
(no lock in the DWH, step 8 of I08). In a child process a hang cannot block the API or the dispatcher; after
DWH_OBJECT_RUN_TIMEOUT_SECONDS (default 3600) the child is stopped, the request is closed FAILED / TIMEOUT and
the next run of the object closes the open run as ABANDONED. The child prints the stack of every thread after
DWH_RUN_OBJECT_STACK_DUMP_SECONDS (default 600), so a hang shows where it waits.

The child keeps its own connections, journals and error reports (I06, I07, I06-05, I13-08); its result lines
(RUN_ID, RUN_RESULT, ODS_RUN_ID, ODS_RESULT ...) are parsed here and linked to the request.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

RUN_OBJECT_SCRIPT = Path(__file__).resolve().with_name("run_object.py")
DEFAULT_TIMEOUT_SECONDS = 3600


def _finish(conn, request_id: int, ok: bool, stg_run_id, ods_run_id, code: str | None, message: str | None) -> str:
    conn.rollback()  # clean transaction state on the dispatcher connection before closing the request
    with conn.cursor() as cur:
        cur.execute("SELECT etl.object_run_request_finish(%s, %s, %s, %s, %s, %s)",
                    (request_id, ok, stg_run_id, ods_run_id, None if ok else (code or "RUN_FAILED"),
                     (message or "")[:4000] or None))
        status = cur.fetchone()[0]
    conn.commit()
    return status


def _split(line: str) -> tuple[str, str | None, str]:
    """'X_RESULT: FAILED | code=ABC | message with | inside' -> ('FAILED', 'ABC', 'message with | inside')."""
    body = line.split(":", 1)[1].strip()
    parts = body.split(" | ", 2)
    status = parts[0].strip()
    code = parts[1].strip()[5:] if len(parts) > 1 and parts[1].strip().startswith("code=") else None
    message = parts[2].strip() if len(parts) > 2 else ""
    return status, code, message


def parse_output(lines: list[str], run_kind: str, exit_code: int | None) -> tuple[bool, int | None, int | None, str | None, str]:
    """Result lines of run_object.main -> (ok, stg_run_id, ods_run_id, result_code, message)."""
    stg_id = ods_id = None
    stg_rows = ods_rows = None
    run_result = ods_result = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("RUN_ID:"):
            stg_id = int(line.split(":", 1)[1].split("|", 1)[0].strip())
        elif line.startswith("ODS_RUN_ID:"):
            ods_id = int(line.split(":", 1)[1].split("|", 1)[0].strip())
        elif line.startswith("ROWS:"):
            stg_rows = line.split(":", 1)[1].strip()
        elif line.startswith("ODS_ROWS:"):
            ods_rows = line.split(":", 1)[1].strip()
        elif line.startswith("RUN_RESULT:"):
            run_result = _split(line)
        elif line.startswith("ODS_RESULT:"):
            ods_result = _split(line)
    parts = []
    if stg_id is not None:
        parts.append(f"STG run {stg_id}: {stg_rows or ''}".rstrip())
    if run_result and run_result[0] != "SUCCESS":
        parts.append(f"STG {run_result[0]}: {run_result[2]}")
        return False, stg_id, None, run_result[1] or "RUN_FAILED", "; ".join(parts)
    if ods_id is not None:
        parts.append(f"ODS run {ods_id}: {ods_rows or ''}".rstrip())
    if ods_result is None:
        parts.append(f"run ended without a result (exit code {exit_code})")
        return False, stg_id, ods_id, "INTERNAL_ERROR", "; ".join(parts)
    status, code, message = ods_result
    if status.startswith("NOT_CONFIGURED"):
        if run_kind == "CONTINUE_ODS":
            return False, stg_id, None, "ODS_NOT_CONFIGURED", "Object has no published ODS configuration"
        parts.append("no published ODS configuration")
        return True, stg_id, None, None, "; ".join(parts)
    if status == "SUCCESS":
        return True, stg_id, ods_id, None, "; ".join(parts)
    parts.append(f"ODS {status}: {message}")
    return False, stg_id, ods_id, code or "RUN_FAILED", "; ".join(parts)


def run_child(source_object_id: int, run_kind: str, *, log: Callable[[str], None],
              timeout: int | None = None, command: list[str] | None = None) -> tuple[list[str], int | None]:
    """Runs one object in a child process; streams its lines to log. Returns (lines, exit code or None on timeout)."""
    env = dict(os.environ)
    env["DWH_RUN_OBJECT_ID"] = str(source_object_id)
    env["DWH_RUN_OBJECT_STAGE"] = "ODS" if run_kind == "CONTINUE_ODS" else "ALL"
    env["DWH_RUN_OBJECT_FULL"] = "1" if run_kind == "FULL_RERUN" else ""
    env["PYTHONIOENCODING"] = "utf-8"
    # The launcher put the project packages on sys.path of this process; the child gets the same path.
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    if timeout is None:
        timeout = int(os.environ.get("DWH_OBJECT_RUN_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
    proc = subprocess.Popen(command or [sys.executable, "-u", str(RUN_OBJECT_SCRIPT)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    lines: list[str] = []

    def reader() -> None:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            lines.append(line)
            log(f"  {line}")
    thread = threading.Thread(target=reader, name="object-run-output", daemon=True)
    thread.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        code = None
    thread.join(timeout=10)
    proc.stdout.close()
    return lines, code


def execute(source_object_id: int, run_kind: str, *, log: Callable[[str], None], timeout: int | None = None,
            command: list[str] | None = None) -> tuple[bool, int | None, int | None, str | None, str]:
    """Runs the steps of one request. Returns (ok, stg_run_id, ods_run_id, result_code, message)."""
    if run_kind not in ("RUN", "FULL_RERUN", "CONTINUE_ODS"):
        return False, None, None, "UNSUPPORTED_RUN_KIND", f"Unsupported run kind {run_kind}"
    try:
        lines, code = run_child(source_object_id, run_kind, log=log, timeout=timeout, command=command)
    except OSError as exc:
        return False, None, None, "INTERNAL_ERROR", f"Run process could not start ({type(exc).__name__})"
    if code is None:
        ok, stg_id, ods_id, _, message = parse_output(lines, run_kind, None)
        limit = timeout or int(os.environ.get("DWH_OBJECT_RUN_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
        return (False, stg_id, ods_id, "TIMEOUT",
                f"Run stopped after {limit} s; the open run is closed as ABANDONED by the next run of the object"
                + (f"; {message}" if message else ""))
    return parse_output(lines, run_kind, code)


def process_one(conn, worker: str, *, log: Callable[[str], None] = lambda m: print(m, flush=True),
                timeout: int | None = None, command: list[str] | None = None) -> bool:
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
    ok, stg_id, ods_id, code, message = execute(source_object_id, run_kind, log=log, timeout=timeout,
                                                command=command)
    status = _finish(conn, request_id, ok, stg_id, ods_id, code, message)
    log(f"OBJECT REQUEST {status} {request_id}" + (f" code={code}" if not ok else "") + f" | {message}")
    return True
