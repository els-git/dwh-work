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
from dwh_api import object_run_admin as api


RUN_OK = ["STAGE: run 14 started (batch_run 45, version 3)", "RUN_ID: 14 | BATCH_RUN_ID: 45",
          "ROWS: read=65 inserted=0 updated=0 unchanged=65 stale=0 deleted=0", "STRATEGY: FULL", "RUN_RESULT: SUCCESS",
          "ODS_RUN_ID: 11 | BATCH_RUN_ID: 46", "ODS_ROWS: read=65 inserted=0 updated=0 unchanged=65", "ODS_RESULT: SUCCESS"]


class ParseOutputTest(unittest.TestCase):
    def test_success_links_both_runs(self):
        ok, s, o, code, msg = worker.parse_output(RUN_OK, "RUN", 0)
        self.assertEqual((ok, s, o, code), (True, 14, 11, None))
        self.assertIn("STG run 14: read=65", msg)
        self.assertIn("ODS run 11: read=65", msg)

    def test_stg_failure_keeps_message_with_separators(self):
        lines = ["RUN_ID: 7 | BATCH_RUN_ID: 70", "ROWS: read=0 inserted=0 updated=0 unchanged=0 stale=0 deleted=0",
                 "RUN_RESULT: FAILED | code=TYPE_CONVERSION | a (TEXT -> bigint): 1 of 3 rows invalid | b (TEXT -> date): 2 of 3"]
        ok, s, o, code, msg = worker.parse_output(lines, "RUN", 1)
        self.assertEqual((ok, s, o, code), (False, 7, None, "TYPE_CONVERSION"))
        self.assertIn("a (TEXT -> bigint): 1 of 3 rows invalid | b (TEXT -> date): 2 of 3", msg)

    def test_ods_failure_and_refusal(self):
        lines = RUN_OK[:-1] + ["ODS_RESULT: FAILED | code=TYPE_CONVERSION | birth_date: 4 of 65 rows invalid"]
        self.assertEqual(worker.parse_output(lines, "RUN", 1)[:4], (False, 14, 11, "TYPE_CONVERSION"))
        refused = worker.parse_output(["RUN_RESULT: REFUSED | code=RUN_REFUSED | Object configuration is DRIFT"], "RUN", 1)
        self.assertEqual(refused[:4], (False, None, None, "RUN_REFUSED"))
        self.assertIn("DRIFT", refused[4])

    def test_not_configured(self):
        lines = RUN_OK[:5] + ["ODS_RESULT: NOT_CONFIGURED (no published ODS version)"]
        self.assertEqual(worker.parse_output(lines, "RUN", 0)[:3], (True, 14, None))
        self.assertEqual(worker.parse_output(["ODS_RESULT: NOT_CONFIGURED (no published ODS version)"], "CONTINUE_ODS", 1)[3],
                         "ODS_NOT_CONFIGURED")

    def test_crash_without_result(self):
        ok, s, o, code, msg = worker.parse_output(["RUN_ID: 3 | BATCH_RUN_ID: 30", "Traceback (most recent call last):"],
                                                  "RUN", 1)
        self.assertEqual((ok, s, code), (False, 3, "INTERNAL_ERROR"))
        self.assertIn("exit code 1", msg)


def script(body: str) -> list[str]:
    return [sys.executable, "-c", body]


class RunChildTest(unittest.TestCase):
    def test_environment_and_streaming(self):
        seen = []
        body = ("import os; print('RUN_ID: 5 | BATCH_RUN_ID: 50'); "
                "print('ENV', os.environ['DWH_RUN_OBJECT_ID'], os.environ['DWH_RUN_OBJECT_STAGE'], "
                "repr(os.environ['DWH_RUN_OBJECT_FULL'])); print('RUN_RESULT: SUCCESS'); "
                "print('ODS_RUN_ID: 6 | BATCH_RUN_ID: 60'); print('ODS_RESULT: SUCCESS')")
        ok, s, o, code, msg = worker.execute(16, "FULL_RERUN", log=seen.append, timeout=30, command=script(body))
        self.assertEqual((ok, s, o), (True, 5, 6))
        self.assertIn("  ENV 16 ALL '1'", seen)
        lines, _ = worker.run_child(16, "CONTINUE_ODS", log=lambda m: None, timeout=30,
                                    command=script("import os; print(os.environ['DWH_RUN_OBJECT_STAGE'], "
                                                   "repr(os.environ['DWH_RUN_OBJECT_FULL']))"))
        self.assertEqual(lines, ["ODS ''"])

    def test_timeout_stops_the_child(self):
        body = "import time; print('RUN_ID: 9 | BATCH_RUN_ID: 90', flush=True); time.sleep(60)"
        ok, s, o, code, msg = worker.execute(16, "RUN", log=lambda m: None, timeout=2, command=script(body))
        self.assertEqual((ok, s, code), (False, 9, "TIMEOUT"))
        self.assertIn("ABANDONED", msg)

    def test_unknown_kind(self):
        self.assertEqual(worker.execute(16, "DELETE", log=lambda m: None)[3], "UNSUPPORTED_RUN_KIND")


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
        self.assertFalse(worker.process_one(conn, "w", log=lambda m: None))
        self.assertEqual(len(conn.calls), 1)

    def test_claim_start_child_finish(self):
        conn = Conn((5, 16, "RUN"))
        body = "print('\\n'.join(%r))" % (RUN_OK,)
        self.assertTrue(worker.process_one(conn, "w", log=lambda m: None, timeout=30, command=script(body)))
        sqls = [c[0] for c in conn.calls]
        self.assertIn("object_run_request_start", sqls[1])
        self.assertEqual(conn.calls[-1][1][:5], (5, True, 14, 11, None))

    def test_failed_request_gets_code(self):
        conn = Conn((6, 16, "RUN"))
        body = "print('RUN_ID: 7 | BATCH_RUN_ID: 70'); print('RUN_RESULT: FAILED | code=SOURCE_ERROR | source: SOURCE_ERROR')"
        worker.process_one(conn, "w", log=lambda m: None, timeout=30, command=script(body))
        self.assertEqual(conn.calls[-1][1][:5], (6, False, 7, None, "SOURCE_ERROR"))


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
