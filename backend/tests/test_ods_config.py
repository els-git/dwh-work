"""I06-05: ODS configuration (white list rules, validation, request model) and the ODS step of the
engine (SQL of transformations, plan, not-configured objects). No database."""

import os
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from dwh_api import ods_config_admin as oca

ETL_SRC = Path(__file__).resolve().parents[2] / "etl" / "src"
if str(ETL_SRC) not in sys.path:
    sys.path.insert(0, str(ETL_SRC))
os.environ.setdefault("DWH_DB_NAME", "unit-test-not-used")
os.environ.setdefault("DWH_ETL_PASSWORD", "unit-test-not-used")

import psycopg  # noqa: E402

import run_object as ro  # noqa: E402


def f(target, transform="COPY", stg=None, stg_type=None, target_type="TEXT", param=None, policy="AUTHORITATIVE", **kw):
    stg = target if stg is None and transform not in oca.NO_STG_COLUMN else stg
    stg_type = (stg_type or target_type) if stg else None
    return {"target_column": target, "transform": transform, "stg_column": stg, "stg_type": stg_type,
            "target_type": target_type, "conversion": kw.get("conversion", "NONE"), "param": param,
            "write_policy": policy, "allow_explicit_null": kw.get("null", False)}


STG = {"schema": "stg", "table": "t"}
STG_COLUMNS = [{"name": "id", "type": "BIGINT"}, {"name": "pin", "type": "TEXT(16)"}, {"name": "birth", "type": "TEXT(16)"},
               {"name": "amount", "type": "TEXT"}, {"name": "extra", "type": "TEXT"}]
TARGET = {"schema": "ods", "table": "t", "columns": [
    {"name": "id", "type": "BIGINT", "not_null": True, "has_default": False},
    {"name": "pin", "type": "TEXT(16)", "not_null": False, "has_default": False},
    {"name": "birth", "type": "DATE", "not_null": False, "has_default": False},
    {"name": "amount", "type": "NUMERIC(12,2)", "not_null": False, "has_default": False},
    {"name": "src", "type": "TEXT", "not_null": False, "has_default": False},
    {"name": "loaded_at", "type": "TIMESTAMPTZ", "not_null": False, "has_default": False},
    {"name": "run_id", "type": "BIGINT", "not_null": False, "has_default": False}]}


def good():
    return {"target_table": "t", "key": ["id"], "fields": [
        f("id", target_type="BIGINT"), f("pin", "TRIM", target_type="TEXT(16)"),
        f("birth", "DATE_FROM_TEXT", stg_type="TEXT(16)", target_type="DATE", param="YYYY-MM-DD"),
        f("amount", "CAST", stg_type="TEXT", target_type="NUMERIC(12,2)", conversion="CONFIRMED"),
        f("src", "CONSTANT", param="OC"), f("loaded_at", "LOADED_AT", target_type="TIMESTAMPTZ", policy="INSERT_ONLY"),
        f("run_id", "LOAD_RUN_ID", target_type="BIGINT", policy="INSERT_ONLY")]}


def codes(issues, severity="ERROR"):
    return sorted(i["code"] for i in issues if i["severity"] == severity)


class ValidationTest(unittest.TestCase):
    def test_good(self):
        issues = oca.validate_ods(good(), STG, STG_COLUMNS, TARGET)
        self.assertEqual(codes(issues), [])
        self.assertEqual(codes(issues, "WARNING"), ["STG_COLUMN_UNMAPPED"])

    def test_missing_parts(self):
        self.assertEqual(codes(oca.validate_ods({"fields": [], "key": []}, None, None, None)),
                         ["NO_FIELDS", "STG_NOT_PUBLISHED", "TARGET_NOT_SELECTED"])
        self.assertIn("TARGET_TABLE_MISSING", codes(oca.validate_ods(good(), STG, STG_COLUMNS, None)))

    def test_transform_rules(self):
        d = good()
        d["fields"][1] = f("pin", "TRIM", stg="id", stg_type="BIGINT", target_type="TEXT(16)")
        d["fields"][2]["target_type"] = "TEXT"
        d["fields"][3]["conversion"] = "NONE"
        d["fields"][4] = f("src", "CONSTANT", target_type="BIGINT", param="abc")
        d["fields"][5]["target_type"] = "DATE"
        found = codes(oca.validate_ods(d, STG, STG_COLUMNS, None))
        for code in ("TRANSFORM_NEEDS_TEXT", "DATE_FROM_TEXT_TYPES", "CONVERSION_NOT_CONFIRMED", "VALUE_INVALID",
                     "TECHNICAL_TYPE"):
            self.assertIn(code, found)

    def test_copy_needs_cast_and_columns(self):
        d = good()
        d["fields"][0] = f("id", stg_type="BIGINT", target_type="INTEGER")
        d["fields"].append(f("ghost", target_type="TEXT"))
        found = codes(oca.validate_ods(d, STG, STG_COLUMNS, TARGET))
        for code in ("TRANSFORM_NEEDS_CAST", "STG_COLUMN_MISSING", "TARGET_COLUMN_MISSING", "TARGET_TYPE_MISMATCH"):
            self.assertIn(code, found)

    def test_key_and_required(self):
        d = good()
        d["key"] = ["src"]
        d["fields"] = [x for x in d["fields"] if x["target_column"] != "id"]
        found = codes(oca.validate_ods(d, STG, STG_COLUMNS, TARGET))
        self.assertIn("KEY_NOT_FROM_STG", found)
        self.assertIn("TARGET_REQUIRED_UNMAPPED", found)

    def test_value_fits(self):
        for value, type_, ok in (("12", "INTEGER", True), ("1.5", "INTEGER", False), ("99999999999", "INTEGER", False),
                                 ("123.45", "NUMERIC(5,2)", True), ("1234.5", "NUMERIC(5,2)", False),
                                 ("true", "BOOLEAN", True), ("yes", "BOOLEAN", False), ("2026-11-11", "DATE", True),
                                 ("11.11.2026", "DATE", False), ("ABC", "TEXT(2)", False), ("AB", "TEXT(2)", True),
                                 ("x", "UUID", False)):
            with self.subTest(value=value, type=type_):
                self.assertEqual(oca.value_fits(value, type_), ok)


class RequestModelTest(unittest.TestCase):
    def test_shape_rules(self):
        oca.OdsFieldConfig(**f("src", "CONSTANT", param="OC"))
        for bad in (f("src", "CONSTANT", stg="pin", stg_type="TEXT", param="OC"), f("src", "CONSTANT"),
                    f("birth", "DATE_FROM_TEXT", target_type="DATE", param="MM/DD/YY"),
                    f("loaded_at", "LOADED_AT", target_type="TIMESTAMPTZ"), f("x", "SQL"),
                    f("x", "COPY", param="v")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                oca.OdsFieldConfig(**bad)


class EngineTest(unittest.TestCase):
    def render(self, composable):
        return composable.as_string(None)

    def test_expressions(self):
        cases = {
            "COPY": ('CAST(s."id" AS bigint)', f("id", target_type="BIGINT")),
            "TRIM": ('CAST(btrim(CAST(s."pin" AS text)) AS varchar(16))', f("pin", "TRIM", target_type="TEXT(16)")),
            "EMPTY_TO_NULL": ("CAST(nullif(btrim(CAST(s.\"pin\" AS text)), '') AS text)", f("pin", "EMPTY_TO_NULL")),
            "DEFAULT": ("coalesce(CAST(s.\"pin\" AS text), CAST('WEB' AS text))", f("pin", "DEFAULT", param="WEB")),
            "CONSTANT": ("CAST('O''C' AS text)", f("src", "CONSTANT", param="O'C")),
            "DATE": ("CAST(to_date(nullif(btrim(CAST(s.\"b\" AS text)), ''), 'DD.MM.YYYY') AS date)",
                     f("b", "DATE_FROM_TEXT", stg_type="TEXT", target_type="DATE", param="DD.MM.YYYY")),
            "RUN": ("CAST(77 AS bigint)", f("r", "LOAD_RUN_ID", target_type="BIGINT", policy="INSERT_ONLY")),
            "AT": ("CAST(now() AS timestamptz)", f("a", "LOADED_AT", target_type="TIMESTAMPTZ", policy="INSERT_ONLY")),
        }
        for name, (expected, field) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.render(ro.ods_expression(field, 77)), expected)
        with self.assertRaises(ro.RunError):
            ro.ods_expression(f("x", "SQL"), 1)

    def test_plan_writes_constants_and_inserts_technical_only(self):
        plan = ro.ods_plan(good())
        self.assertEqual(plan.target_schema, "ods")
        self.assertEqual(len(plan.columns), 7)
        self.assertEqual(sorted(x["target_column"] for x in plan.authoritative), ["amount", "birth", "pin", "src"])

    def test_not_configured_and_refused(self):
        class Diag:
            def __init__(self, text):
                self.message_primary = text

        class Err(psycopg.Error):
            pass

        class Cur:
            def __init__(self, text):
                self.text = text

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                e = Err("x")
                e.diag_override = Diag(self.text)
                raise e

        Err.diag = property(lambda self: self.diag_override)

        class Conn:
            def __init__(self, text):
                self.text = text
                self.closed = False

            def cursor(self):
                return Cur(self.text)

            def rollback(self):
                pass

            def close(self):
                self.closed = True

        conn = Conn("Object has no published ODS configuration")
        self.assertIsNone(ro.run_ods(16, 7, dwh_connect=lambda: conn))
        self.assertTrue(conn.closed)
        with self.assertRaises(ro.RunError) as ctx:
            ro.run_ods(16, 7, dwh_connect=lambda: Conn("STG run is not a successful run of this object"))
        self.assertEqual(ctx.exception.code, "ODS_REFUSED")


if __name__ == "__main__":
    unittest.main()
