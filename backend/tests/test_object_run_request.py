"""I08: object run requests — dispatcher worker (fake engine and connection), full rerun flag, API errors."""

import os
import sys
import unittest
from pathlib import Path

ETL_SRC = Path(__file__).resolve().parents[2] / "etl" / "src"
if str(ETL_SRC) not in sys.path:
    sys.path.insert(0, str(ETL_SRC))
os.environ.setdefault("DWH_DB_NAME", "unit-test-not-used")
os.environ.setdefault("DWH_ETL_PASSWORD", "unit-test-not-used")
os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

import psycopg
from fastapi import HTTPException

import object_request_worker as worker
import run_object as ro
from dwh_api import object_run_admin as api


def stg(status="SUCCESS", run_id=7, code=None, message=None):
    return ro.RunResult(run_id=run_id, batch_run_id=70, status=status, rows_read=3, rows_inserted=1, rows_updated=1,
                        rows_unchanged=1, error_code=code, message=message)


def ods(status="SUCCESS", ods_run_id=9, code=None, message=None):
    return ro.OdsResult(ods_run_id=ods_run_id, batch_run_id=90, status=status, rows_read=3, rows_inserted=0,
                        rows_updated=2, rows_unchanged=1, error_code=code, message=message)


class Engine:
    """Replaces run_object / run_ods of the engine and records the calls."""

    def __init__(self, stg_result=None, ods_result="unset", stg_error=None):
        self.stg_result = stg_result if stg_result is not None else stg()
        self.ods_result = ods() if ods_result == "unset" else ods_result
        self.stg_error = stg_error
        self.calls = []

    def __enter__(self):
        self.saved = (ro.run_object, ro.run_ods)

        def run_object(obj, **kw):
            self.calls.append(("stg", obj, kw.get("force_full")))
            if self.stg_error:
                raise self.stg_error
            return self.stg_result

        def run_ods(obj, stg_run_id, **kw):
            self.calls.append(("ods", obj, stg_run_id))
            return self.ods_result
        ro.run_object, ro.run_ods = run_object, run_ods
        return self

    def __exit__(self, *exc):
        ro.run_object, ro.run_ods = self.saved
        return False


def run(kind, engine):
    with engine:
        return worker.execute(16, kind, dwh_connect=lambda: None, read_credential=lambda c: "", log=lambda m: None)


class ExecuteTest(unittest.TestCase):
    def test_run_success_links_both_runs(self):
        e = Engine()
        ok, s, o, code, msg = run("RUN", e)
        self.assertEqual((ok, s, o, code), (True, 7, 9, None))
        self.assertEqual(e.calls, [("stg", 16, False), ("ods", 16, 7)])
        self.assertIn("STG run 7: read 3", msg)
        self.assertIn("ODS run 9: read 3", msg)

    def test_full_rerun_forces_full_read(self):
        e = Engine()
        self.assertTrue(run("FULL_RERUN", e)[0])
        self.assertEqual(e.calls[0], ("stg", 16, True))

    def test_stg_failure_stops_before_ods(self):
        e = Engine(stg_result=stg("FAILED", code="TYPE_CONVERSION", message="born (TEXT -> date): 1 of 3 rows invalid"))
        ok, s, o, code, msg = run("RUN", e)
        self.assertEqual((ok, s, o, code), (False, 7, None, "TYPE_CONVERSION"))
        self.assertEqual([c[0] for c in e.calls], ["stg"])
        self.assertIn("born (TEXT -> date)", msg)

    def test_ods_failure_keeps_stg_link(self):
        e = Engine(ods_result=ods("FAILED", code="TYPE_CONVERSION", message="birth_date: 4 of 65 rows invalid"))
        ok, s, o, code, msg = run("RUN", e)
        self.assertEqual((ok, s, o, code), (False, 7, 9, "TYPE_CONVERSION"))
        self.assertIn("STG run 7", msg)
        self.assertIn("ODS run 9 FAILED: birth_date", msg)

    def test_no_ods_configuration_is_success(self):
        ok, s, o, code, msg = run("RUN", Engine(ods_result=None))
        self.assertEqual((ok, s, o), (True, 7, None))
        self.assertIn("no published ODS configuration", msg)

    def test_continue_runs_ods_alone(self):
        e = Engine()
        ok, s, o, code, msg = run("CONTINUE_ODS", e)
        self.assertEqual((ok, s, o), (True, None, 9))
        self.assertEqual(e.calls, [("ods", 16, None)])

    def test_continue_without_ods_configuration_fails(self):
        self.assertEqual(run("CONTINUE_ODS", Engine(ods_result=None))[:4], (False, None, None, "ODS_NOT_CONFIGURED"))

    def test_refused_and_unexpected(self):
        refused = run("RUN", Engine(stg_error=ro.RunError("RUN_REFUSED", "Object configuration is DRIFT")))
        self.assertEqual(refused[3], "RUN_REFUSED")
        self.assertIn("DRIFT", refused[4])
        unexpected = run("RUN", Engine(stg_error=KeyError("secret-value")))
        self.assertEqual(unexpected[3], "INTERNAL_ERROR")
        self.assertNotIn("secret-value", unexpected[4])


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        if "object_run_request_claim" in sql:
            self.row = self.conn.claim
        elif "object_run_request_finish" in sql:
            self.row = ("SUCCESS" if params[1] else "FAILED",)
        else:
            self.row = (True,)

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, claim):
        self.claim = claim
        self.calls = []
        self.commits = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class ProcessOneTest(unittest.TestCase):
    def test_empty_queue(self):
        conn = Conn(None)
        self.assertFalse(worker.process_one(conn, "w", dwh_connect=lambda: None, read_credential=lambda c: "",
                                            log=lambda m: None))
        self.assertEqual(len(conn.calls), 1)

    def test_claim_start_execute_finish(self):
        conn = Conn((5, 16, "RUN"))
        with Engine():
            self.assertTrue(worker.process_one(conn, "w", dwh_connect=lambda: None, read_credential=lambda c: "",
                                               log=lambda m: None))
        sqls = [c[0] for c in conn.calls]
        self.assertIn("object_run_request_start", sqls[1])
        finish = conn.calls[-1][1]
        self.assertEqual(finish[:5], (5, True, 7, 9, None))

    def test_failed_request_gets_code(self):
        conn = Conn((6, 16, "RUN"))
        with Engine(stg_result=stg("FAILED", code="SOURCE_ERROR", message="source: SOURCE_ERROR")):
            worker.process_one(conn, "w", dwh_connect=lambda: None, read_credential=lambda c: "", log=lambda m: None)
        finish = conn.calls[-1][1]
        self.assertEqual(finish[:5], (6, False, 7, None, "SOURCE_ERROR"))


class Diag:
    def __init__(self, message):
        self.message_primary = message
        self.sqlstate = "P0001"


class DbError(psycopg.Error):
    pass


DbError.diag = property(lambda self: Diag(self.args[0]))


class ApiErrorTest(unittest.TestCase):
    def status(self, message):
        with self.assertRaises(HTTPException) as ctx:
            api._raise_request_error(DbError(message))
        return ctx.exception.status_code

    def test_state_errors_are_client_errors(self):
        self.assertEqual(self.status("Object already has an active run request"), 409)
        self.assertEqual(self.status("Only QUEUED run requests can be cancelled"), 409)
        self.assertEqual(self.status("Continue is possible only after a failed ODS step that follows a successful STG run"), 422)
        self.assertEqual(self.status("Object has no published READY configuration"), 422)
        self.assertEqual(self.status("Legacy CRM sources are run by their own ETL"), 422)
        self.assertEqual(self.status("Authentication required"), 401)

    def test_payload_kinds(self):
        self.assertEqual(api.ObjectRunRequestCreate(source_object_id=16, run_kind="FULL_RERUN").run_kind, "FULL_RERUN")
        with self.assertRaises(Exception):
            api.ObjectRunRequestCreate(source_object_id=16, run_kind="DELETE")


if __name__ == "__main__":
    unittest.main()
