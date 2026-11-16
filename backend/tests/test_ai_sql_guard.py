"""I17 step 5: the SQL of the model runs only as one SELECT over whitelist objects allowed to the user."""
from __future__ import annotations

import unittest

from dwh_api.ai_sql_guard import (AllowedObject, SqlRejected, check_plan, check_text, extract_sql, limited,
                                  plan_relations)

CUR = AllowedObject(1, "dm", "v_retail_application_current")
DM = {"v_retail_application_current", "retail_application_history", "v_retail_application_source",
      "salary_project"}


def code(sql, allowed=(CUR,)):
    try:
        check_text(sql, allowed, DM)
    except SqlRejected as exc:
        return exc.code
    return "OK"


class TextTests(unittest.TestCase):
    def test_good_queries(self):
        for sql in ("SELECT sales_channel_name_ru, count(*) FROM dm.v_retail_application_current GROUP BY 1",
                    "with c as (select * from dm.v_retail_application_current) select count(*) from c;",
                    "SELECT count(*) FROM v_retail_application_current WHERE status_name_ru = 'Одобрено; ok'",
                    # acceptance R04 (16.11.2026): the model named the view alias "dm"
                    "SELECT dm.branch_name_ru, count(dm.application_id) FROM dm.v_retail_application_current dm "
                    "GROUP BY dm.branch_name_ru"):
            self.assertEqual(code(sql), "OK", sql)
        statement, ids = check_text("SELECT 1 FROM dm.v_retail_application_current;", [CUR], DM)
        self.assertEqual(ids, [1])
        self.assertFalse(statement.endswith(";"))

    def test_refusals(self):
        cases = {
            "": "AI_SQL_EMPTY",
            "-- NO_DATA": "AI_NO_DATA",
            "-- DENIED": "AI_OBJECT_NOT_ALLOWED",
            "DELETE FROM dm.v_retail_application_current": "AI_SQL_NOT_SELECT",
            "SELECT 1 FROM dm.v_retail_application_current; DROP TABLE x": "AI_SQL_MULTIPLE",
            "SELECT * FROM dm.v_retail_application_current -- x": "AI_SQL_COMMENT",
            "SELECT * FROM dm.v_retail_application_current /* x */": "AI_SQL_COMMENT",
            "SELECT * FROM dm.salary_project": "AI_OBJECT_NOT_ALLOWED",
            "SELECT * FROM salary_project": "AI_OBJECT_NOT_ALLOWED",
            "SELECT * FROM dm.retail_application_history": "AI_OBJECT_NOT_ALLOWED",
            "SELECT dm.salary FROM dm.salary_project dm": "AI_OBJECT_NOT_ALLOWED",
            "SELECT * FROM app.user_account": "AI_OBJECT_NOT_ALLOWED",
            "SELECT * FROM pg_catalog.pg_authid": "AI_SQL_FORBIDDEN",
            "SELECT pg_sleep(100) FROM dm.v_retail_application_current": "AI_SQL_FORBIDDEN",
            "SELECT current_setting('x') FROM dm.v_retail_application_current": "AI_SQL_FORBIDDEN",
            'SELECT * FROM "dm"."salary_project"': "AI_SQL_QUOTED_NAME",
            "SELECT * INTO t FROM dm.v_retail_application_current": "AI_SQL_FORBIDDEN",
            "SELECT $$x$$ FROM dm.v_retail_application_current": "AI_SQL_DOLLAR",
            "SELECT 1": "AI_SQL_NO_OBJECT",
            "WITH d AS (DELETE FROM dm.v_retail_application_current RETURNING *) SELECT * FROM d": "AI_SQL_FORBIDDEN",
            "SELECT * FROM information_schema.tables, dm.v_retail_application_current": "AI_OBJECT_NOT_ALLOWED",
            "SELECT 1 FROM dm.v_retail_application_current FOR UPDATE": "AI_SQL_FORBIDDEN",
        }
        for sql, expected in cases.items():
            self.assertEqual(code(sql), expected, sql)

    def test_no_allowed_objects(self):
        self.assertEqual(code("SELECT * FROM dm.v_retail_application_current", ()), "AI_OBJECT_NOT_ALLOWED")

    def test_extract_and_limit(self):
        self.assertEqual(extract_sql("text\n```sql\nSELECT 1\n```\nmore"), "SELECT 1")
        self.assertEqual(extract_sql("SELECT 2"), "SELECT 2")
        self.assertTrue(limited("SELECT 1", 1000).endswith("LIMIT 1001"))


class PlanTests(unittest.TestCase):
    PLAN = [{"Plan": {"Node Type": "Aggregate", "Plans": [
        {"Node Type": "Seq Scan", "Schema": "dm", "Relation Name": "retail_application_history"}]}}]

    def test_plan_relations_and_closure(self):
        self.assertEqual(plan_relations(self.PLAN), {("dm", "retail_application_history")})
        check_plan(self.PLAN, {("dm", "v_retail_application_current"), ("dm", "retail_application_history")})
        with self.assertRaises(SqlRejected):
            check_plan(self.PLAN, {("dm", "v_retail_application_current")})


if __name__ == "__main__":
    unittest.main()
