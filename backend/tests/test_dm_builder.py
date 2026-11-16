"""I19 step 5: SQL of a DM mart (dm_sql), engine (dm_mart_worker), queueing after ODS, API error mapping."""

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

import dm_mart_worker as worker
import run_object
from dwh_api import dm_builder_admin as api
from dwh_api import dm_sql
from dwh_api.dm_sql import Definition, Field, FieldSource, Filter, Source

TYPES = {("cl", "id"): "bigint", ("cl", "name"): "text", ("cl", "st"): "text", ("cl", "mgr"): "bigint",
         ("acc", "cust"): "bigint", ("acc", "amt"): "numeric(18,2)", ("acc", "state"): "text",
         ("ph", "cid"): "integer", ("ph", "phone"): "text", ("ref", "code"): "text", ("ref", "nm"): "text",
         ("man", "id"): "bigint", ("man", "fio"): "text"}


def definition(filters=None) -> Definition:
    return Definition("m1", 5, 2, [
        Source("c", "cl", "GRAIN", "id"), Source("p", "ph", "SAME_KEY", "cid"), Source("a", "acc", "CHILD", "cust"),
        Source("s", "ref", "LOOKUP", "code", "c", "st"), Source("m", "man", "LOOKUP", "id", "c", "mgr")], [
        Field("id", "bigint", True, [FieldSource("c", "id")]),
        Field("name", "varchar(50)", False, [FieldSource("c", "name")]),
        Field("contact", "text", False, [FieldSource("p", "phone"), FieldSource("c", "name")]),
        Field("total", "numeric(18,2)", False, [FieldSource("a", "amt", "SUM")]),
        Field("n", "bigint", False, [FieldSource("a", "amt", "COUNT")]),
        Field("status", "text", False, [FieldSource("s", "nm")]),
        Field("manager", "text", False, [FieldSource("m", "fio")])],
        filters if filters is not None else [Filter("a", "state", "IN", ["OPEN", "X"]), Filter("c", "id", ">", 10)])


class FillSqlTest(unittest.TestCase):
    def test_shape(self):
        text, params = dm_sql.fill_sql(definition(), TYPES)
        self.assertTrue(text.startswith('INSERT INTO "dm"."m1" ("id", "name", "contact", "total", "n", "status", "manager", '
                                        '"dm_loaded_at")'))
        # child aggregated per key before the join; filters are parameters cast to the column type
        self.assertIn('LEFT JOIN (SELECT "cust" AS "__key", sum("amt") AS "__a1", count("amt") AS "__a2" FROM "ods"."acc" '
                      'WHERE "state" = ANY(CAST(%s AS text[])) GROUP BY "cust") AS "a" ON "a"."__key" = "c"."id"', text)
        self.assertIn('FROM (SELECT * FROM "ods"."cl" WHERE "id" > CAST(%s AS bigint)) AS "c"', text)
        self.assertIn('LEFT JOIN (SELECT * FROM "ods"."ph") AS "p" ON "p"."cid" = "c"."id"', text)
        self.assertIn('AS "s" ON "s"."code" = "c"."st"', text)
        self.assertIn('coalesce(CAST("p"."phone" AS text), CAST("c"."name" AS text)) AS "contact"', text)
        self.assertIn('CAST("c"."name" AS text) AS "name"', text)  # varchar(n): too long fails the INSERT, no cut
        self.assertIn('CAST(coalesce("a"."__a2", 0) AS bigint) AS "n"', text)
        self.assertEqual(params, ["10", ["OPEN", "X"]])  # grain subquery first, then the joins in order

    def test_render_inlines_values(self):
        text, params = dm_sql.fill_sql(definition(), TYPES)
        shown = dm_sql.render(text, params)
        self.assertNotIn("%s", shown)
        self.assertIn("CAST('10' AS bigint)", shown)
        self.assertIn("CAST(ARRAY['OPEN', 'X'] AS text[])", shown)

    def test_rules(self):
        d = definition()
        d.fields[3].sources[0].aggregate = "NONE"
        with self.assertRaisesRegex(dm_sql.DefinitionError, "needs an aggregate"):
            dm_sql.fill_sql(d, TYPES)
        d = definition()
        d.fields[1].sources[0].aggregate = "SUM"
        with self.assertRaisesRegex(dm_sql.DefinitionError, "only for CHILD"):
            dm_sql.fill_sql(d, TYPES)
        d = definition()
        d.fields[1].data_type = "text; drop table x"
        with self.assertRaises(dm_sql.DefinitionError):
            dm_sql.fill_sql(d, TYPES)
        with self.assertRaises(dm_sql.DefinitionError):
            dm_sql.qi('a"b')
        d = definition([Filter("a", "state", "IN", "OPEN")])
        with self.assertRaisesRegex(dm_sql.DefinitionError, "list"):
            dm_sql.fill_sql(d, TYPES)

    def test_like_and_null_filters(self):
        text, params = dm_sql.fill_sql(definition([Filter("c", "name", "LIKE", "Ив%"), Filter("c", "st", "IS NOT NULL")]), TYPES)
        self.assertIn('WHERE "name"::text LIKE %s AND "st" IS NOT NULL', text)
        self.assertEqual(params, ["Ив%"])

    def test_key_checks(self):
        checks = dm_sql.key_checks(definition(), TYPES)
        self.assertEqual([(k.code, k.alias, k.column) for k in checks],
                         [("DM_KEY_NOT_UNIQUE", "c", "id"), ("DM_KEY_NOT_UNIQUE", "p", "cid"),
                          ("DM_LOOKUP_NOT_UNIQUE", "s", "code"), ("DM_LOOKUP_NOT_UNIQUE", "m", "id")])
        self.assertIn("HAVING count(*) > 1", checks[0].sql)
        self.assertEqual(checks[0].params, ["10"])

    def test_missing_columns(self):
        types = {k: v for k, v in TYPES.items() if k != ("acc", "amt") and k[0] != "man"}
        self.assertEqual(dm_sql.missing_columns(definition(), types), ["ods.man", "ods.acc.amt"])


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.rowcount = 0
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, statement, params=None):
        self.conn.statements.append((statement, params))
        answer = self.conn.answer(statement, params)
        if isinstance(answer, Exception):
            raise answer
        self.description, self._rows, self.rowcount = answer

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, answer):
        self.answer = answer
        self.statements = []
        self.commits = self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def desc(*names):
    return [(n,) for n in names]


class EngineTest(unittest.TestCase):
    def answers(self, duplicate=False):
        def answer(statement, params):
            if "pg_attribute" in statement:
                return desc("table_name", "column_name", "typ"), [(t, c, ty) for (t, c), ty in TYPES.items()], 0
            if "statement_timeout" in statement:
                return desc("x"), [("x",)], 1
            if "IS NULL" in statement and "count(*) FROM" in statement:
                return desc("count"), [(0,)], 1
            if "HAVING count(*) > 1" in statement:
                rows = [("7", 3, 1)] if duplicate and '"ph"' in statement else []
                return desc("key_value", "rows", "duplicate_keys"), rows, len(rows)
            if statement.startswith("DELETE"):
                return None, [], 4
            if statement.startswith("INSERT"):
                return None, [], 5
            raise AssertionError(statement)
        return answer

    def test_success_reloads_in_one_transaction(self):
        conn = FakeConn(self.answers())
        result = worker.build(conn, definition(), timeout=60)
        self.assertEqual((result.ok, result.rows_deleted, result.rows_inserted), (True, 4, 5))
        kinds = [s.split()[0] for s, _ in conn.statements]
        self.assertEqual(kinds[-2:], ["DELETE", "INSERT"])
        self.assertEqual(conn.commits, 1)
        self.assertIn(("SELECT set_config('statement_timeout', %s, true)", ("60000",)), conn.statements)

    def test_duplicate_key_stops_before_delete(self):
        conn = FakeConn(self.answers(duplicate=True))
        result = worker.build(conn, definition(), timeout=60)
        self.assertEqual((result.ok, result.error_code), (False, "DM_KEY_NOT_UNIQUE"))
        self.assertEqual(result.diagnostic["examples"], [{"key": "7", "rows": 3}])
        self.assertIn("cid in ods.ph (source p)", result.message)
        self.assertFalse(any(s.startswith("DELETE") for s, _ in conn.statements))
        self.assertEqual(conn.commits, 0)

    def test_data_error_is_coded(self):
        base = self.answers()

        def answer(statement, params):
            if statement.startswith("INSERT"):
                return psycopg.errors.StringDataRightTruncation("value too long for type character varying(50)")
            return base(statement, params)
        result = worker.build(FakeConn(answer), definition(), timeout=60)
        self.assertEqual((result.ok, result.error_code), (False, "DM_DATA_ERROR"))
        self.assertIn("value too long", result.message)

    def test_missing_source(self):
        def answer(statement, params):
            if "pg_attribute" in statement:
                return desc("table_name", "column_name", "typ"), [], 0
            raise AssertionError(statement)
        result = worker.build(FakeConn(answer), definition(), timeout=60)
        self.assertEqual(result.error_code, "DM_SOURCE_MISSING")

    def test_process_one_empty_queue(self):
        conn = FakeConn(lambda s, p: (desc("request_id"), [], 0))
        self.assertFalse(worker.process_one(conn, "w", log=lambda m: None))


class EnqueueTest(unittest.TestCase):
    def test_queued_and_tolerated_failure(self):
        logs = []
        conn = FakeConn(lambda s, p: (desc("n"), [(2,)], 1))
        self.assertEqual(run_object.enqueue_marts(conn, "t1", logs.append), 2)
        self.assertEqual(conn.statements, [("SELECT etl.dm_mart_enqueue_after_ods(%s)", ("t1",))])
        conn = FakeConn(lambda s, p: psycopg.errors.UndefinedFunction("function etl.dm_mart_enqueue_after_ods does not exist"))
        self.assertEqual(run_object.enqueue_marts(conn, "t1", logs.append), 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertIn("WARNING: DM marts were not queued", logs[-1])


class ApiErrorTest(unittest.TestCase):
    def status(self, message):
        exc = psycopg.errors.RaiseException(message)
        with self.assertRaises(HTTPException) as caught:
            api._raise(exc)
        return caught.exception.status_code

    def test_mapping(self):
        self.assertEqual(self.status("Integration administration access denied"), 403)
        self.assertEqual(self.status("Mart already has a draft version"), 409)
        self.assertEqual(self.status("Mart version not found"), 404)
        self.assertEqual(self.status("Key type TEXT is not compatible with the keys of the entity (NUMBER)"), 422)
        self.assertEqual(self.status("Column ods.t.c not found"), 422)

    def test_definition_document_round_trip(self):
        doc = api.definition_document(definition(), 3)
        self.assertEqual(doc["sources"][3], {"alias": "s", "table": "ref", "kind": "LOOKUP", "ref_alias": "c",
                                             "ref_column": "st", "lookup_column": "code"})
        api.VersionDefinition.model_validate({k: v for k, v in doc.items()})


if __name__ == "__main__":
    unittest.main()
