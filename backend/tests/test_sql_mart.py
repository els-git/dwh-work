"""I23: text checks, type mapping and generated SQL of marts from a ready query (dwh_api.sql_mart)."""
import unittest

from dwh_api import sql_mart
from dwh_api.sql_mart import CheckResult, Column, SqlMartError


class NormalizeTest(unittest.TestCase):
    def test_trailing_semicolons_and_spaces_dropped(self):
        self.assertEqual(sql_mart.normalize("  SELECT a FROM ods.t ;; \n"), "SELECT a FROM ods.t")

    def test_select_with_compound_and_comments_accepted(self):
        for text in ("with x as (select 1) select * from x", "(SELECT a FROM ods.t) UNION ALL (SELECT a FROM ods.u)",
                     "-- comment\n/* block */ SELECT a FROM ods.t"):
            self.assertTrue(sql_mart.normalize(text))

    def test_not_a_query_refused(self):
        for text in ("DELETE FROM ods.t", "update ods.t set a = 1", "CALL x()", "-- only a comment"):
            with self.assertRaises(SqlMartError) as ctx:
                sql_mart.normalize(text)
            self.assertEqual(ctx.exception.code, "DM_SQL_NOT_SELECT")

    def test_empty_and_long_refused(self):
        with self.assertRaises(SqlMartError) as ctx:
            sql_mart.normalize(" ; ")
        self.assertEqual(ctx.exception.code, "DM_SQL_EMPTY")
        with self.assertRaises(SqlMartError) as ctx:
            sql_mart.normalize("SELECT " + "a" * sql_mart.MAX_SQL_CHARS)
        self.assertEqual(ctx.exception.code, "DM_SQL_TOO_LONG")

    def test_wrapped_keeps_a_trailing_line_comment_inside(self):
        self.assertEqual(sql_mart.wrapped("SELECT a FROM ods.t -- c"), "SELECT * FROM (\nSELECT a FROM ods.t -- c\n) AS q")


class TypesTest(unittest.TestCase):
    def test_mapping(self):
        cases = {"bigint": "bigint", "integer": "integer", "smallint": "smallint", "numeric": "numeric",
                 "numeric(18,2)": "numeric(18,2)", "numeric(120,2)": "numeric", "double precision": "numeric",
                 "date": "date", "timestamp without time zone": "timestamp", "timestamp with time zone": "timestamptz",
                 "boolean": "boolean", "character varying(20)": "varchar(20)", "character(3)": "varchar(3)",
                 "character varying(20000)": "text", "character varying": "text", "text": "text", "uuid": "text",
                 "jsonb": "text", "interval": "text"}
        for source, expected in cases.items():
            self.assertEqual(sql_mart.map_type(source), expected, source)

    def test_varchar_read_as_text(self):
        self.assertEqual(sql_mart.cast_type("varchar(20)"), "text")
        self.assertEqual(sql_mart.cast_type("numeric(18,2)"), "numeric(18,2)")


class PlanTest(unittest.TestCase):
    def test_relations_of_nested_plan(self):
        plan = [{"Plan": {"Node Type": "Append", "Plans": [
            {"Node Type": "Seq Scan", "Schema": "ods", "Relation Name": "a"},
            {"Node Type": "Hash Join", "Plans": [{"Relation Name": "b", "Schema": "app"}]}]}}]
        self.assertEqual(sql_mart.plan_tables(plan), {("ods", "a"), ("app", "b")})


class FieldsTest(unittest.TestCase):
    def result(self, *columns):
        return CheckResult("SELECT 1", ["t"], list(columns))

    def test_fields_from_the_check_with_key_and_titles(self):
        r = self.result(Column("id", "bigint", "bigint"), Column("name", "text", "text"))
        self.assertEqual(sql_mart.fields_document(r, "id", {"name": " Имя ", "x": "ignored"}),
                         [{"name": "id", "title": None, "type": "bigint", "is_key": True},
                          {"name": "name", "title": "Имя", "type": "text", "is_key": False}])

    def test_column_problems_and_key_refused(self):
        r = self.result(Column("id", "bigint", "bigint"), Column("?column?", "integer", "integer", "bad name"))
        with self.assertRaises(SqlMartError) as ctx:
            sql_mart.fields_document(r, "id")
        self.assertEqual(ctx.exception.code, "DM_SQL_COLUMNS_INVALID")
        r = self.result(Column("id", "bigint", "bigint"), Column("name", "text", "text"))
        with self.assertRaises(SqlMartError) as ctx:
            sql_mart.fields_document(r, "other")
        self.assertEqual(ctx.exception.code, "DM_SQL_KEY_INVALID")
        with self.assertRaises(SqlMartError):
            sql_mart.fields_document(self.result(Column("id", "bigint", "bigint")), "id")


class GeneratedSqlTest(unittest.TestCase):
    def test_read_sql_casts_fields_and_adds_load_time(self):
        text = sql_mart.read_sql("SELECT a, b FROM ods.t", [("a", "bigint"), ("b", "varchar(10)")])
        self.assertEqual(text, 'SELECT CAST(q."a" AS bigint) AS "a", CAST(q."b" AS text) AS "b", now() AS "dm_loaded_at" '
                               'FROM (\nSELECT a, b FROM ods.t\n) AS q')

    def test_key_checks(self):
        self.assertIn('WHERE q."id" IS NULL', sql_mart.key_null_sql("SELECT id FROM ods.t", "id"))
        dup = sql_mart.key_duplicates_sql("SELECT id FROM ods.t", "id", 10)
        self.assertIn('GROUP BY q."id" HAVING count(*) > 1', dup)
        self.assertTrue(dup.endswith("LIMIT 10"))

    def test_password_required(self):
        import os
        saved = os.environ.pop(sql_mart.PASSWORD_ENV, None)
        try:
            with self.assertRaises(SqlMartError) as ctx:
                sql_mart.password()
            self.assertEqual(ctx.exception.code, "DM_SQL_NOT_CONFIGURED")
        finally:
            if saved is not None:
                os.environ[sql_mart.PASSWORD_ENV] = saved


if __name__ == "__main__":
    unittest.main()
