"""I06 object run engine: plan, error mapping, run flow with fake connections (no database)."""

import os
import sys
import unittest
from pathlib import Path

ETL_SRC = Path(__file__).resolve().parents[2] / "etl" / "src"
if str(ETL_SRC) not in sys.path:
    sys.path.insert(0, str(ETL_SRC))
os.environ.setdefault("DWH_DB_NAME", "unit-test-not-used")
os.environ.setdefault("DWH_ETL_PASSWORD", "unit-test-not-used")

import psycopg

import run_object as ro

SECRET = "synthetic-source-Ж-1"


def field(column, type_="BIGINT", policy="AUTHORITATIVE", null=False, source=None):
    return {"target_column": column, "source_column": source if source is not None else column,
            "source_type": type_, "target_type": type_, "conversion": "NONE", "write_policy": policy,
            "allow_explicit_null": null}


def definition(fields=None, key=None):
    return {"target_schema": "stg", "target_table": "t",
            "fields": fields or [field("id"), field("amount"), field("note", "TEXT", "INSERT_ONLY"),
                                 field("skip", "TEXT", "NO_WRITE")],
            "key": key or ["id"]}


class Diag:
    def __init__(self, sqlstate, message="value 'SECRET-VALUE' is bad", context=None, column=None):
        self.sqlstate = sqlstate
        self.message_primary = message
        self.context = context
        self.column_name = column


class DbError(psycopg.Error):
    pass


def db_error(sqlstate, **kw):
    exc = DbError("x")
    exc.diag_override = Diag(sqlstate, **kw)
    return exc


DbError.diag = property(lambda self: self.diag_override)


class PlanTest(unittest.TestCase):
    def test_columns_and_policies(self):
        plan = ro.Plan(**{k: v for k, v in definition().items()})
        self.assertEqual(plan.columns, ["id", "amount", "note"])
        self.assertEqual([f["target_column"] for f in plan.authoritative], ["amount"])

    def test_invalid(self):
        for d in (definition(fields=[field("id")], key=["x"]),
                  definition(fields=[field("id", policy="NO_WRITE"), field("a")], key=["id"])):
            with self.assertRaises(ro.RunError) as ctx:
                ro.Plan(**d)
            self.assertEqual(ctx.exception.code, "CONFIG_INVALID")
        with self.assertRaises(ro.RunError):
            ro.Plan("stg", "t", [], ["id"])

    def test_structure(self):
        plan = ro.Plan(**definition())
        ro.check_structure(plan, {"id": "BIGINT", "amount": "BIGINT", "note": "TEXT", "extra": "TEXT"})
        for columns, column in (({"id": "BIGINT", "note": "TEXT"}, "amount"),
                                ({"id": "BIGINT", "amount": "TEXT", "note": "TEXT"}, "amount")):
            with self.assertRaises(ro.RunError) as ctx:
                ro.check_structure(plan, columns)
            self.assertEqual((ctx.exception.code, ctx.exception.column), ("SCHEMA_DRIFT", column))
        with self.assertRaises(ro.RunError) as ctx:
            ro.check_structure(plan, {})
        self.assertEqual(ctx.exception.code, "SOURCE_OBJECT_MISSING")


class ErrorMappingTest(unittest.TestCase):
    def test_codes_and_no_values(self):
        cases = {"22001": "VALUE_TOO_LONG", "22P02": "TYPE_CONVERSION", "23514": "TARGET_CHECK",
                 "57014": "TIMEOUT", "XX000": "DATABASE_ERROR"}
        for state, code in cases.items():
            err = ro.database_error(db_error(state, context="COPY dwh_object_run_stage, line 3, column pin: \"SECRET-VALUE\""),
                                    "stage")
            self.assertEqual(err.code, code)
            self.assertNotIn("SECRET-VALUE", err.message)
            self.assertEqual(err.column, "pin")
        self.assertEqual(ro.database_error(db_error("XX000"), "source").code, "SOURCE_ERROR")
        self.assertEqual(ro.database_error(db_error("23502", column="surname"), "apply").column, "surname")


# ------------------------------------------------------------------ fake flow


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else repr(query)
        self.conn.calls.append((text, params))
        handler = next((h for k, h in self.conn.handlers.items() if k in text), None)
        self._row = handler(params) if handler else None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class Conn:
    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **kw):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def finished(self):
        return [p for t, p in self.calls if "object_run_finish" in t]


BEGIN = {"run_id": 7, "batch_run_id": 70, "job_run_id": 700, "version_id": 3, "version_no": 1,
         "definition_hash": "0" * 64, "definition": definition(),
         "source": {"source_system_id": 5, "code": "SRC", "source_type": "POSTGRESQL", "source_schema": "public",
                    "source_object": "t", "connection_config": {"host": "db"}}}


def dwh_conn(begin=BEGIN, refuse=False):
    def begin_handler(params):
        if refuse:
            raise db_error("P0001", message="Object configuration is DRIFT, resolve it before a run")
        return (begin,)
    return Conn({"object_run_begin": begin_handler, "object_run_finish": lambda p: ("SUCCESS" if p[1] else "FAILED",),
                 "pg_attribute": lambda p: [("id", "bigint"), ("amount", "bigint"), ("note", "text")]})


class FlowTest(unittest.TestCase):
    def test_refused_run(self):
        conn = dwh_conn(refuse=True)
        with self.assertRaises(ro.RunError) as ctx:
            ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET)
        self.assertEqual(ctx.exception.code, "RUN_REFUSED")
        self.assertIn("DRIFT", ctx.exception.message)
        self.assertTrue(conn.closed)

    def test_credential_failure_closes_run_failed(self):
        conn = dwh_conn()

        def no_credential(code):
            raise RuntimeError("decrypt " + SECRET)
        result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=no_credential)
        self.assertEqual((result.status, result.error_code), ("FAILED", "CREDENTIAL_UNAVAILABLE"))
        (params,) = conn.finished()
        self.assertEqual(params[0], 7)
        self.assertFalse(params[1])
        self.assertEqual(params[6], "CREDENTIAL_UNAVAILABLE")
        self.assertNotIn(SECRET, repr(conn.calls))

    def test_source_connection_gets_settings_and_password(self):
        conn = dwh_conn()
        seen = {}

        def connect(config, password):
            seen.update(config=config, password=password)
            raise ro.RunError("SOURCE_CONNECT_FAILED", "Source connection failed (SQLSTATE 08001)")
        result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET, source_connect_fn=connect)
        self.assertEqual(result.error_code, "SOURCE_CONNECT_FAILED")
        self.assertEqual((seen["config"], seen["password"]), ({"host": "db"}, SECRET))
        self.assertNotIn(SECRET, repr(conn.calls))
        self.assertTrue(conn.closed)

    def test_target_drift_stops_before_credential(self):
        conn = dwh_conn()
        conn.handlers["pg_attribute"] = lambda p: [("id", "bigint"), ("amount", "text"), ("note", "text")]
        asked = []
        result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: asked.append(c) or SECRET)
        self.assertEqual((result.error_code, asked), ("TARGET_DRIFT", []))

    def test_database_error_during_apply_is_coded(self):
        conn = dwh_conn()

        class Source:
            def close(self):
                pass
        original = (ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows)
        try:
            ro.source_columns = lambda c, s, t: {"id": "BIGINT", "amount": "BIGINT", "note": "TEXT"}
            ro.stage_rows = lambda d, p, rows: 3
            ro.validate_key = lambda d, p: None

            def apply(d, p, run):
                raise db_error("23514", message="row (1, 'SECRET-VALUE') violates check")
            ro.apply_rows = apply
            result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET,
                                   source_connect_fn=lambda c, p: Source())
        finally:
            ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows = original
        self.assertEqual((result.status, result.error_code), ("FAILED", "TARGET_CHECK"))
        self.assertGreaterEqual(conn.rollbacks, 1)
        self.assertNotIn("SECRET-VALUE", repr(conn.finished()))

    def test_success_counts_and_single_commit_with_data(self):
        conn = dwh_conn()

        class Source:
            def close(self):
                pass
        original = (ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows)
        try:
            ro.source_columns = lambda c, s, t: {"id": "BIGINT", "amount": "BIGINT", "note": "TEXT"}
            ro.stage_rows = lambda d, p, rows: 10
            ro.validate_key = lambda d, p: None
            ro.apply_rows = lambda d, p, run: (4, 2)
            result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET,
                                   source_connect_fn=lambda c, p: Source())
        finally:
            ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows = original
        self.assertEqual((result.status, result.rows_read, result.rows_inserted, result.rows_updated,
                          result.rows_unchanged), ("SUCCESS", 10, 4, 2, 4))
        (params,) = conn.finished()
        self.assertEqual(params[:6], (7, True, 10, 4, 2, 4))
        self.assertEqual(conn.commits, 2)  # begin, then data + finish together


if __name__ == "__main__":
    unittest.main()
