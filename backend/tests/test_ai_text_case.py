"""AI report: names typed by the user are compared without regard to case (request of the user 20.11.2026)."""
import unittest

from dwh_api.ai_core import fold_text, fold_text_equality, report_text_columns
from dwh_api.ai_sql_guard import AllowedObject

TEXT = {"last_name", "first_name", "middle_name", "status", "comment"}


class FoldTextTest(unittest.TestCase):
    def test_azerbaijani_letters(self):
        self.assertEqual(fold_text("Əsgərov Elşad"), "ƏSGƏROV ELŞAD")
        self.assertEqual(fold_text("İsmayılov namiq"), "ISMAYILOV NAMIQ")
        self.assertEqual(fold_text("çöüğ"), "ÇÖÜĞ")

    def test_case_from_the_screenshot(self):
        sql = ("SELECT count(application_id) FROM dm.cdp_clients "
               "WHERE last_name = 'Əsgərov' AND first_name = 'Elşad'")
        self.assertEqual(fold_text_equality(sql, TEXT),
                         "SELECT count(application_id) FROM dm.cdp_clients WHERE "
                         "translate(upper(last_name), 'İ', 'I') = 'ƏSGƏROV' AND "
                         "translate(upper(first_name), 'İ', 'I') = 'ELŞAD'")

    def test_like_ilike_in_and_alias(self):
        out = fold_text_equality("SELECT * FROM dm.c c WHERE c.first_name ILIKE '%namiq%' "
                                 "AND c.status IN ('active', 'Closed') AND c.last_name NOT LIKE 'Ə%'", TEXT)
        self.assertIn("translate(upper(c.first_name), 'İ', 'I') LIKE '%NAMIQ%'", out)
        self.assertIn("translate(upper(c.status), 'İ', 'I') IN ('ACTIVE', 'CLOSED')", out)
        self.assertIn("translate(upper(c.last_name), 'İ', 'I') NOT LIKE 'Ə%'", out)

    def test_untouched(self):
        for sql in ("SELECT * FROM dm.c WHERE application_id = '12'",          # not a text column
                    "SELECT * FROM dm.c WHERE last_name = '123'",                # no letters
                    "SELECT * FROM dm.c WHERE comment = 'last_name = ''a'''",    # text inside a string
                    "SELECT * FROM dm.c WHERE upper(last_name) = 'X'",           # an expression, not a column
                    "SELECT * FROM dm.c WHERE created_at >= now() - interval '1 year'"):
            expected = sql if "comment" not in sql else (
                "SELECT * FROM dm.c WHERE translate(upper(comment), 'İ', 'I') = 'LAST_NAME = ''A'''")
            self.assertEqual(fold_text_equality(sql, TEXT), expected)

    def test_text_columns_query(self):
        class Cur:
            def execute(self, sql, params):
                self.params = params

            def fetchall(self):
                return [{"column_name": "LAST_NAME"}, {"column_name": "status"}]
        cur = Cur()
        self.assertEqual(report_text_columns(cur, [AllowedObject(1, "dm", "cdp_clients")]), {"last_name", "status"})
        self.assertEqual(cur.params[0], ["cdp_clients"])


if __name__ == "__main__":
    unittest.main()
