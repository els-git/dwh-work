"""I22 step 3: query building and export files of the mart view (pure parts, no database)."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

os.environ.setdefault("DWH_DB_NAME", "unit-test-not-used")
os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

from dwh_api.mart_view import (
    EXPORT_LIMITS, Column, ColumnFilter, MartViewError, Sort, ViewRequest, build_pdf, build_xlsx, excel_value,
    filters_text, json_value, order_clause, select_sql, text_value, too_many_rows, where_clause,
)

COLUMNS = [Column("id", "bigint", "number"), Column("name", "character varying(100)", "text"),
           Column("created_at", "timestamp with time zone", "date"), Column("flag", "boolean", "boolean"),
           Column('od"d', "text", "text")]


class WhereTest(unittest.TestCase):
    def test_operators(self):
        where, params = where_clause(COLUMNS, [
            ColumnFilter(column="id", op="eq", value=" 5 "),
            ColumnFilter(column="name", op="contains", value="a%b_c"),
            ColumnFilter(column="created_at", op="gte", value="2026-01-01"),
            ColumnFilter(column="id", op="lte", value="9"),
            ColumnFilter(column="name", op="empty"),
            ColumnFilter(column="flag", op="not_empty"),
            ColumnFilter(column="name", op="eq", value="Ann"),
        ])
        self.assertEqual(where, ' WHERE "id" = CAST(%s AS bigint) AND "name"::text ILIKE %s'
                                ' AND "created_at" >= CAST(%s AS timestamp with time zone)'
                                ' AND "id" <= CAST(%s AS bigint) AND ("name" IS NULL OR "name"::text = \'\')'
                                ' AND "flag" IS NOT NULL AND "name"::text = %s')
        self.assertEqual(params, ["5", "%a\\%b\\_c%", "2026-01-01", "9", "Ann"])

    def test_no_filters(self):
        self.assertEqual(where_clause(COLUMNS, []), ("", []))

    def test_quoted_identifier(self):
        where, _ = where_clause(COLUMNS, [ColumnFilter(column='od"d', op="empty")])
        self.assertIn('"od""d"', where)

    def test_unknown_column_is_refused(self):
        with self.assertRaises(MartViewError) as ctx:
            where_clause(COLUMNS, [ColumnFilter(column="id; DROP TABLE x", op="empty")])
        self.assertEqual(ctx.exception.code, "MART_FILTER_COLUMN")

    def test_value_required(self):
        with self.assertRaises(MartViewError) as ctx:
            where_clause(COLUMNS, [ColumnFilter(column="name", op="contains", value="  ")])
        self.assertEqual(ctx.exception.code, "MART_FILTER_VALUE")

    def test_range_on_text_refused(self):
        with self.assertRaises(MartViewError) as ctx:
            where_clause(COLUMNS, [ColumnFilter(column="name", op="gte", value="a")])
        self.assertEqual(ctx.exception.code, "MART_FILTER_OPERATOR")


class OrderTest(unittest.TestCase):
    def test_default_first_column(self):
        self.assertEqual(order_clause(COLUMNS, None), ' ORDER BY "id"')

    def test_sort_with_tie_breaker(self):
        self.assertEqual(order_clause(COLUMNS, Sort(column="name", desc=True)),
                         ' ORDER BY "name" DESC NULLS LAST, "id"')
        self.assertEqual(order_clause(COLUMNS, Sort(column="id")), ' ORDER BY "id" ASC NULLS LAST')

    def test_unknown_sort_column(self):
        with self.assertRaises(MartViewError) as ctx:
            order_clause(COLUMNS, Sort(column="nope"))
        self.assertEqual(ctx.exception.code, "MART_SORT_COLUMN")

    def test_select(self):
        count, select, params = select_sql("dm", "cdp_clients", COLUMNS,
                                           ViewRequest(filters=[ColumnFilter(column="id", op="eq", value="1")]))
        self.assertEqual(count, 'SELECT count(*) AS n FROM "dm"."cdp_clients" WHERE "id" = CAST(%s AS bigint)')
        self.assertTrue(select.startswith('SELECT "id", "name", "created_at", "flag", "od""d" FROM "dm"."cdp_clients"'))
        self.assertTrue(select.endswith('ORDER BY "id"'))
        self.assertEqual(params, ["1"])


class LimitTest(unittest.TestCase):
    def test_limits(self):
        self.assertEqual(EXPORT_LIMITS, {"CSV": 1_000_000, "XLSX": 1_000_000, "PDF": 5_000})

    def test_too_many_rows_explains(self):
        exc = too_many_rows("PDF", 44_300, "Клиенты")
        self.assertEqual(exc.status, 409)
        self.assertEqual(exc.code, "MART_EXPORT_TOO_MANY_ROWS")
        self.assertEqual(exc.extra["rows"], 44_300)
        self.assertEqual(exc.extra["limit"], 5_000)
        self.assertEqual(exc.extra["other_formats"], ["CSV", "XLSX"])
        self.assertEqual(too_many_rows("CSV", 2_000_000, "x").extra["other_formats"], [])


class ValueTest(unittest.TestCase):
    def test_values(self):
        moment = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(json_value(Decimal("1.50")), "1.50")
        self.assertEqual(json_value(date(2026, 1, 2)), "2026-01-02")
        self.assertIsNone(json_value(None))
        self.assertEqual(text_value(None), "")
        self.assertEqual(text_value(True), "true")
        self.assertIsNone(excel_value(moment).tzinfo)
        self.assertEqual(filters_text(ViewRequest(filters=[ColumnFilter(column="id", op="gte", value="3")])), "id ≥ 3")


ROWS = [{"id": i, "name": f"Имя {i}", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "flag": i % 2 == 0,
         'od"d': None} for i in range(30)]


class FileTest(unittest.TestCase):
    def test_xlsx(self):
        from openpyxl import load_workbook
        handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        handle.close()
        try:
            self.assertEqual(build_xlsx(iter(ROWS), COLUMNS, "Клиенты: [тест]", handle.name), 30)
            sheet = load_workbook(handle.name).active
            self.assertEqual([c.value for c in sheet[1]], [c.name for c in COLUMNS])
            self.assertEqual(sheet.max_row, 31)
            self.assertEqual(sheet["B2"].value, "Имя 0")
        finally:
            os.unlink(handle.name)

    def test_pdf_wide_mart(self):
        wide = [Column(f"c{i}", "text", "text") for i in range(25)]
        rows = [{f"c{i}": "значение " * 10 for i in range(25)} for _ in range(50)]
        body = build_pdf(rows, wide, "Клиенты (dm.cdp_clients)", "c1 ∋ x")
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertGreater(len(body), 5000)


if __name__ == "__main__":
    unittest.main()
