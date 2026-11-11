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
        original = (ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows, ro.estimated_rows)
        try:
            ro.source_columns = lambda c, s, t: {"id": "BIGINT", "amount": "BIGINT", "note": "TEXT"}
            ro.stage_rows = lambda d, p, rows, log=None: 3
            ro.estimated_rows = lambda c, s, t: 3
            ro.validate_key = lambda d, p: None

            def apply(d, p, run):
                raise db_error("23514", message="row (1, 'SECRET-VALUE') violates check")
            ro.apply_rows = apply
            result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET,
                                   source_connect_fn=lambda c, p: Source())
        finally:
            ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows, ro.estimated_rows = original
        self.assertEqual((result.status, result.error_code), ("FAILED", "TARGET_CHECK"))
        self.assertGreaterEqual(conn.rollbacks, 1)
        self.assertNotIn("SECRET-VALUE", repr(conn.finished()))

    def test_success_counts_and_single_commit_with_data(self):
        conn = dwh_conn()

        class Source:
            def close(self):
                pass
        original = (ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows, ro.estimated_rows)
        try:
            ro.source_columns = lambda c, s, t: {"id": "BIGINT", "amount": "BIGINT", "note": "TEXT"}
            ro.stage_rows = lambda d, p, rows, log=None: 10
            ro.estimated_rows = lambda c, s, t: 10
            ro.validate_key = lambda d, p: None
            ro.apply_rows = lambda d, p, run: (4, 2, 1, 0)
            result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET,
                                   source_connect_fn=lambda c, p: Source())
        finally:
            ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows, ro.estimated_rows = original
        self.assertEqual((result.status, result.rows_read, result.rows_inserted, result.rows_updated,
                          result.rows_unchanged, result.rows_stale), ("SUCCESS", 10, 4, 2, 3, 1))
        (params,) = conn.finished()
        self.assertEqual(params[:6], (7, True, 10, 4, 2, 3))
        (inc,) = [p for t, p in conn.calls if "object_run_record_increment" in t]
        self.assertEqual(inc, (7, None, None, 1, 0))
        self.assertEqual(conn.commits, 2)  # begin, then data + finish together


def incremental(change="changed_at", change_type="TIMESTAMPTZ", flag=None, overlap=10):
    d = definition(fields=[field("id"), field("amount"), field(change, change_type), field("is_deleted", "BOOLEAN")])
    d["strategy"] = {"load_strategy": "INCREMENTAL", "change_column": change, "overlap_minutes": overlap,
                     "delete_flag_column": flag}
    return d


class IncrementalTest(unittest.TestCase):
    def render(self, composable):
        return composable.as_string(None)

    def test_plan_strategy(self):
        plan = ro.Plan(**incremental(flag="is_deleted"))
        self.assertEqual((plan.load_strategy, plan.change_field["target_column"], plan.overlap_minutes,
                          plan.delete_flag), ("INCREMENTAL", "changed_at", 10, "is_deleted"))
        full = ro.Plan(**definition())
        self.assertEqual((full.load_strategy, full.change_field, full.delete_flag), ("FULL", None, None))
        for bad in ({"load_strategy": "INCREMENTAL"}, {"change_column": "nope"}, {"delete_flag_column": "nope"}):
            d = incremental()
            d["strategy"] = bad
            with self.assertRaises(ro.RunError) as ctx:
                ro.Plan(**d)
            self.assertEqual(ctx.exception.code, "CONFIG_INVALID")

    def test_filter(self):
        plan = ro.Plan(**incremental())
        self.assertIsNone(ro.increment_filter(plan, None))  # first run reads everything
        self.assertIsNone(ro.increment_filter(ro.Plan(**definition()), "2026-01-01"))
        text = self.render(ro.increment_filter(plan, "2026-11-05 10:00:00+04"))
        self.assertIn('"changed_at" >= CAST(\'2026-11-05 10:00:00+04\' AS timestamptz) - make_interval(mins => 10)', text)
        text = self.render(ro.increment_filter(ro.Plan(**incremental("seq", "BIGINT")), "42"))
        self.assertEqual(text, '"seq" >= CAST(\'42\' AS bigint)')

    def run_with(self, begin, estimate):
        conn = dwh_conn(begin=begin)
        conn.handlers["pg_attribute"] = lambda p: [("id", "bigint"), ("amount", "bigint"),
                                                   ("changed_at", "timestamp with time zone"), ("is_deleted", "boolean")]
        conn.handlers["max("] = lambda p: ("2026-11-05 12:00:00+04",)

        class Source:
            def close(self):
                pass
        seen = {}
        original = (ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows, ro.estimated_rows, ro.read_source)
        try:
            ro.source_columns = lambda c, s, t: {"id": "BIGINT", "amount": "BIGINT", "changed_at": "TIMESTAMPTZ",
                                                 "is_deleted": "BOOLEAN"}
            ro.stage_rows = lambda d, p, rows, log=None: 5
            ro.estimated_rows = lambda c, s, t: estimate
            ro.validate_key = lambda d, p: None
            ro.apply_rows = lambda d, p, run: (1, 1, 1, 1)
            ro.read_source = lambda c, s, t, p, limit, where=None: seen.update(where=where) or iter(())
            result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET,
                                   source_connect_fn=lambda c, p: Source())
        finally:
            (ro.source_columns, ro.stage_rows, ro.validate_key, ro.apply_rows, ro.estimated_rows,
             ro.read_source) = original
        return result, conn, seen

    def test_incremental_run_records_watermark(self):
        begin = dict(BEGIN, definition=incremental(flag="is_deleted"), watermark="2026-11-05 10:00:00+04")
        result, conn, seen = self.run_with(begin, 2_000_000)  # limit applies only to full reads
        self.assertEqual(result.status, "SUCCESS")
        self.assertIsNotNone(seen["where"])
        (inc,) = [p for t, p in conn.calls if "object_run_record_increment" in t]
        self.assertEqual(inc, (7, "2026-11-05 10:00:00+04", "2026-11-05 12:00:00+04", 1, 1))
        self.assertEqual((result.rows_unchanged, result.rows_deleted), (2, 1))

    def test_first_incremental_run_is_full(self):
        begin = dict(BEGIN, definition=incremental(), watermark=None)
        result, conn, seen = self.run_with(begin, 2_000_000)
        self.assertEqual((result.error_code, seen), ("ROW_LIMIT", {}))
        result, conn, seen = self.run_with(begin, 5)
        self.assertIsNone(seen["where"])
        (inc,) = [p for t, p in conn.calls if "object_run_record_increment" in t]
        self.assertEqual(inc[1:3], (None, "2026-11-05 12:00:00+04"))


class EstimateTest(unittest.TestCase):
    def test_estimate_over_limit_stops_before_read(self):
        conn = dwh_conn()

        class Source:
            def close(self):
                pass
        original = (ro.source_columns, ro.estimated_rows, ro.read_source)
        read = []
        try:
            ro.source_columns = lambda c, s, t: {"id": "BIGINT", "amount": "BIGINT", "note": "TEXT"}
            ro.estimated_rows = lambda c, s, t: 2_000_000
            ro.read_source = lambda *a: read.append(a) or iter(())
            messages = []
            result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: SECRET,
                                   source_connect_fn=lambda c, p: Source(), log=messages.append)
        finally:
            ro.source_columns, ro.estimated_rows, ro.read_source = original
        self.assertEqual((result.error_code, read), ("ROW_LIMIT", []))
        self.assertTrue(any("estimated rows 2000000" in m for m in messages))
        self.assertNotIn(SECRET, " ".join(messages))


if __name__ == "__main__":
    unittest.main()


class ConversionReportTest(unittest.TestCase):
    """I13-08: report of invalid values (R1 = B: values shown) and the finish arguments of package 014."""

    def fields(self):
        return [{"column": "birth_date", "rule": "DATE_FROM_TEXT DD.MM.YYYY -> date", "invalid_rows": 47,
                 "values": [{"value": "99.99.9999", "count": 43}, {"value": "birthD", "count": 4}],
                 "more_values": False, "keys": [{"id": i} for i in range(1, 11)]},
                {"column": "amount", "rule": "TEXT -> bigint", "invalid_rows": 1,
                 "values": [{"value": "x" * 80, "count": 1}], "more_values": True, "keys": [{"id": 2, "sub": "a"}]}]

    def test_message_fields_values_keys(self):
        err = ro.conversion_error("ODS", 65, self.fields())
        self.assertEqual(err.code, "TYPE_CONVERSION")
        first, second = err.message.split(" | ")
        self.assertEqual(first, "birth_date (DATE_FROM_TEXT DD.MM.YYYY -> date): 47 of 65 rows invalid; "
                                "values: '99.99.9999' x43, 'birthD' x4; keys: id=1, id=2, id=3, id=4, id=5, id=6, "
                                "id=7, id=8, id=9, id=10 (10 of 47)")
        self.assertIn("'" + "x" * 50 + "…' x1, ...", second)
        self.assertIn("keys: (id=2, sub=a)", second)
        self.assertNotIn("(1 of 1)", second)

    def test_error_log_parts(self):
        err = ro.conversion_error("STG", 65, self.fields())
        self.assertEqual((err.column, err.record_key, err.bad_value), ("birth_date", {"id": 1}, "99.99.9999"))
        self.assertEqual(err.diagnostic["stage"], "STG")
        self.assertEqual(err.diagnostic["total_rows"], 65)
        self.assertEqual([f["column"] for f in err.diagnostic["fields"]], ["birth_date", "amount"])
        self.assertNotIn("more_values", err.diagnostic["fields"][0])
        args = ro._finish_args(False, err)
        self.assertEqual(args[0], "TYPE_CONVERSION")
        self.assertEqual(args[3:5], ("birth_date", "99.99.9999"))
        self.assertEqual(args[5].obj["fields"][0]["invalid_rows"], 47)

    def test_success_and_plain_errors_send_no_details(self):
        self.assertEqual(ro._finish_args(True, None), (None, None, None, None, None, None))
        args = ro._finish_args(False, ro.RunError("SOURCE_ERROR", "source: SOURCE_ERROR"))
        self.assertEqual(args[4:], (None, None))

    def test_field_without_values_or_keys(self):
        err = ro.conversion_error("ODS", 3, [{"column": "c", "rule": "CONSTANT -> date", "invalid_rows": 3,
                                              "values": [], "more_values": False, "keys": []}])
        self.assertEqual(err.message, "c (CONSTANT -> date): 3 of 3 rows invalid")
        self.assertIsNone(err.record_key)
        self.assertIsNone(err.bad_value)

    def test_finish_uses_twelve_parameter_variants(self):
        conn = dwh_conn()
        result = ro.run_object(16, dwh_connect=lambda: conn, read_credential=lambda c: (_ for _ in ()).throw(RuntimeError()))
        (params,) = conn.finished()
        self.assertEqual(len(params), 12)
        self.assertEqual(result.error_code, "CREDENTIAL_UNAVAILABLE")
