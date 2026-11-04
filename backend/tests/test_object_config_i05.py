"""I05: normalized types, validation, drift, change proposals, API flow (fake DB)."""

import os
import unittest

os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

from fastapi import HTTPException
from pydantic import ValidationError

from dwh_api import field_types as ft
from dwh_api import object_config_admin as oca


class FieldTypesTest(unittest.TestCase):
    def test_normalize_postgres(self):
        cases = {
            "text": "TEXT", "character varying": "TEXT", "character varying(200)": "TEXT(200)",
            "character(3)": "TEXT(3)", "smallint": "INTEGER", "integer": "INTEGER", "bigint": "BIGINT",
            "numeric": "NUMERIC", "numeric(18,2)": "NUMERIC(18,2)", "numeric(10)": "NUMERIC(10,0)",
            "real": "FLOAT", "double precision": "FLOAT", "boolean": "BOOLEAN", "date": "DATE",
            "timestamp without time zone": "TIMESTAMP", "timestamp(3) with time zone": "TIMESTAMPTZ",
            "timestamp with time zone": "TIMESTAMPTZ", "time without time zone": "TIME", "uuid": "UUID",
            "jsonb": "JSON", "json": "JSON", "bytea": "BYTES", "integer[]": "OTHER", "point": "OTHER",
        }
        for pg, expected in cases.items():
            self.assertEqual(ft.normalize_postgres(pg), expected, pg)

    def test_postgres_type_round_trip(self):
        for t in ("TEXT", "TEXT(50)", "INTEGER", "BIGINT", "NUMERIC", "NUMERIC(18,2)", "FLOAT", "BOOLEAN",
                  "DATE", "TIME", "TIMESTAMP", "TIMESTAMPTZ", "UUID", "JSON", "BYTES"):
            self.assertEqual(ft.normalize_postgres(ft.postgres_type(t)), t, t)
        with self.assertRaises(ValueError):
            ft.postgres_type("OTHER")
        with self.assertRaises(ValueError):
            ft.parse("text")

    def test_matrix_examples_of_plan(self):
        self.assertEqual(ft.compatibility("INTEGER", "BIGINT"), ft.SAFE)
        self.assertEqual(ft.compatibility("BIGINT", "INTEGER"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("TEXT", "DATE"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("BOOLEAN", "DATE"), ft.INCOMPATIBLE)
        self.assertEqual(ft.compatibility("TEXT(10)", "TEXT(10)"), ft.SAME)

    def test_matrix_more(self):
        self.assertEqual(ft.compatibility("UUID", "TEXT"), ft.SAFE)
        self.assertEqual(ft.compatibility("TEXT(10)", "TEXT(20)"), ft.SAFE)
        self.assertEqual(ft.compatibility("TEXT(20)", "TEXT(10)"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("TEXT", "TEXT(10)"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("NUMERIC(10,2)", "NUMERIC(12,2)"), ft.SAFE)
        self.assertEqual(ft.compatibility("NUMERIC(12,2)", "NUMERIC(10,2)"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("NUMERIC(10,2)", "NUMERIC(10,0)"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("INTEGER", "NUMERIC(10,0)"), ft.SAFE)
        self.assertEqual(ft.compatibility("BIGINT", "NUMERIC"), ft.SAFE)
        self.assertEqual(ft.compatibility("NUMERIC", "BIGINT"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("FLOAT", "NUMERIC(18,2)"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("DATE", "TIMESTAMP"), ft.SAFE)
        self.assertEqual(ft.compatibility("TIMESTAMP", "TIMESTAMPTZ"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("JSON", "TEXT"), ft.SAFE)
        self.assertEqual(ft.compatibility("BYTES", "TEXT"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("UUID", "INTEGER"), ft.INCOMPATIBLE)
        self.assertEqual(ft.compatibility("OTHER", "TEXT"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("OTHER", "OTHER"), ft.CONDITIONAL)
        self.assertEqual(ft.compatibility("OTHER", "INTEGER"), ft.INCOMPATIBLE)

    def test_conversion_for(self):
        self.assertEqual(ft.conversion_for(ft.SAME, False), "NONE")
        self.assertEqual(ft.conversion_for(ft.SAFE, False), "SAFE")
        self.assertIsNone(ft.conversion_for(ft.CONDITIONAL, False))
        self.assertEqual(ft.conversion_for(ft.CONDITIONAL, True), "CONFIRMED")
        self.assertIsNone(ft.conversion_for(ft.INCOMPATIBLE, True))


def field(target, source=None, st="BIGINT", tt="BIGINT", conversion="NONE", policy="AUTHORITATIVE", null=False):
    return {"target_column": target, "source_column": source if source is not None else target, "source_type": st,
            "target_type": tt, "conversion": conversion, "write_policy": policy, "allow_explicit_null": null}


SNAPSHOT = {"metadata_capture_id": 7, "captured_at": None, "columns": [
    {"name": "id", "position": 1, "formatted_type": "bigint", "type": "BIGINT", "nullable": False, "primary_key": True},
    {"name": "pin", "position": 2, "formatted_type": "character varying(20)", "type": "TEXT(20)", "nullable": True,
     "primary_key": False},
    {"name": "created", "position": 3, "formatted_type": "timestamp without time zone", "type": "TIMESTAMP",
     "nullable": True, "primary_key": False},
]}
TARGET = {"schema": "stg", "table": "asan", "columns": [
    {"name": "id", "formatted_type": "bigint", "type": "BIGINT", "not_null": True, "has_default": False},
    {"name": "pin", "formatted_type": "text", "type": "TEXT", "not_null": False, "has_default": False},
    {"name": "created", "formatted_type": "timestamp with time zone", "type": "TIMESTAMPTZ", "not_null": False,
     "has_default": False},
]}


def good_definition():
    return {"target_schema": "stg", "target_table": "asan", "key": ["id"], "fields": [
        field("id"),
        field("pin", st="TEXT(20)", tt="TEXT", conversion="SAFE"),
        field("created", st="TIMESTAMP", tt="TIMESTAMPTZ", conversion="CONFIRMED"),
    ]}


def codes(issues, severity="ERROR"):
    return sorted(i["code"] for i in issues if i["severity"] == severity)


class ValidationTest(unittest.TestCase):
    def test_good_definition(self):
        issues = oca.validate_definition(good_definition(), SNAPSHOT, TARGET)
        self.assertEqual(codes(issues), [])
        self.assertFalse(oca.has_errors(issues))

    def test_missing_target_snapshot_fields(self):
        issues = oca.validate_definition({"fields": [], "key": []}, None, None)
        self.assertEqual(codes(issues), ["NO_FIELDS", "SNAPSHOT_MISSING", "TARGET_NOT_SELECTED"])
        d = good_definition()
        self.assertIn("TARGET_TABLE_MISSING", codes(oca.validate_definition(d, SNAPSHOT, None)))

    def test_conversion_rules(self):
        d = good_definition()
        d["fields"][2]["conversion"] = "NONE"
        d["fields"][1]["conversion"] = "NONE"
        self.assertEqual(codes(oca.validate_definition(d, SNAPSHOT, TARGET)),
                         ["CONVERSION_MISMATCH", "CONVERSION_NOT_CONFIRMED"])
        d = good_definition()
        d["fields"][2].update(target_type="BOOLEAN")
        self.assertIn("TYPE_INCOMPATIBLE", codes(oca.validate_definition(d, SNAPSHOT, None)))

    def test_snapshot_and_target_mismatch(self):
        d = good_definition()
        d["fields"][1]["source_type"] = "TEXT"
        d["fields"][2]["target_type"] = "TIMESTAMP"
        d["fields"][2]["conversion"] = "SAFE"
        d["fields"].append(field("extra", source="nope"))
        found = codes(oca.validate_definition(d, SNAPSHOT, TARGET))
        for code in ("SOURCE_TYPE_CHANGED", "TARGET_TYPE_MISMATCH", "SOURCE_COLUMN_MISSING", "TARGET_COLUMN_MISSING"):
            self.assertIn(code, found)

    def test_key_rules(self):
        d = good_definition()
        d["key"] = []
        self.assertIn("KEY_EMPTY", codes(oca.validate_definition(d, SNAPSHOT, TARGET)))
        d = good_definition()
        d["key"] = ["pin", "ghost"]
        d["fields"][1]["allow_explicit_null"] = True
        issues = oca.validate_definition(d, SNAPSHOT, TARGET)
        self.assertEqual(codes(issues), ["KEY_FIELD_MISSING", "KEY_NULL_ALLOWED"])
        self.assertIn("KEY_SOURCE_NULLABLE", codes(issues, "WARNING"))
        d = good_definition()
        d["fields"][0]["write_policy"] = "NO_WRITE"
        found = codes(oca.validate_definition(d, SNAPSHOT, TARGET))
        self.assertIn("KEY_NOT_WRITTEN", found)
        self.assertIn("TARGET_REQUIRED_UNMAPPED", found)

    def test_warnings(self):
        d = good_definition()
        d["fields"] = d["fields"][:2]
        target = {**TARGET, "columns": [dict(c, not_null=True, has_default=c["name"] != "pin") for c in TARGET["columns"]]}
        issues = oca.validate_definition(d, SNAPSHOT, target)
        self.assertEqual(codes(issues), [])
        self.assertEqual(codes(issues, "WARNING"), ["NULLABLE_TO_NOT_NULL", "SOURCE_COLUMN_UNMAPPED"])

    def test_duplicates(self):
        d = good_definition()
        d["fields"].append(field("id"))
        d["fields"].append(field("id2", source="id"))
        issues = oca.validate_definition(d, SNAPSHOT, None)
        self.assertIn("DUPLICATE_TARGET_COLUMN", codes(issues))
        self.assertIn("SOURCE_COLUMN_REUSED", codes(issues, "WARNING"))

    def test_request_model_rejects_bad_names(self):
        with self.assertRaises(ValidationError):
            oca.FieldConfig(target_column="Bad Name", source_type="TEXT", target_type="TEXT")
        with self.assertRaises(ValidationError):
            oca.FieldConfig(target_column="a", source_type="text", target_type="TEXT")
        with self.assertRaises(ValidationError):
            oca.ObjectConfigDocument(target_schema="public", target_table="t")
        with self.assertRaises(ValidationError):
            oca.FieldConfig(target_column="a", source_type="TEXT", target_type="TEXT", write_policy="DROP")


class DriftTest(unittest.TestCase):
    def test_no_drift_against_baseline(self):
        baseline = oca.baseline_of(SNAPSHOT)
        self.assertEqual(oca.drift_changes(baseline, good_definition(), SNAPSHOT), [])

    def test_changes(self):
        baseline = oca.baseline_of(SNAPSHOT)
        snap = {"metadata_capture_id": 8, "columns": [
            {"name": "id", "type": "INTEGER"}, {"name": "created", "type": "TIMESTAMP"}, {"name": "new", "type": "TEXT"}]}
        changes = oca.drift_changes(baseline, good_definition(), snap)
        kinds = sorted((c["change"], c["source_column"]) for c in changes)
        self.assertEqual(kinds, [("ADDED", "new"), ("REMOVED", "pin"), ("TYPE_CHANGED", "id")])
        self.assertTrue(next(c for c in changes if c["change"] == "REMOVED")["mapped"])

    def test_without_baseline_and_snapshot(self):
        self.assertEqual(oca.drift_changes(None, good_definition(), None), [{"change": "SNAPSHOT_MISSING"}])
        snap = {"metadata_capture_id": 8, "columns": [{"name": "id", "type": "BIGINT"}, {"name": "x", "type": "TEXT"}]}
        kinds = sorted(c["source_column"] for c in oca.drift_changes(None, good_definition(), snap))
        self.assertEqual(kinds, ["created", "pin"])


class ProposalTest(unittest.TestCase):
    def test_create_table(self):
        kind, ddl = oca.build_proposal(good_definition(), None, "object 16 (public.asan)")
        self.assertEqual(kind, "CREATE_TABLE")
        self.assertIn('CREATE TABLE "stg"."asan" (', ddl)
        self.assertIn('"id" bigint NOT NULL', ddl)
        self.assertIn('"pin" text,', ddl)
        self.assertIn('"created" timestamptz,', ddl)
        self.assertIn('CONSTRAINT "pk_asan" PRIMARY KEY ("id")', ddl)
        self.assertTrue(ddl.startswith("-- I05 change proposal for object 16"))

    def test_add_columns(self):
        d = good_definition()
        d["fields"].append(field("amount", source="amount", st="NUMERIC(18,2)", tt="NUMERIC(18,2)"))
        kind, ddl = oca.build_proposal(d, TARGET, "x")
        self.assertEqual(kind, "ADD_COLUMNS")
        self.assertIn('ALTER TABLE "stg"."asan" ADD COLUMN "amount" numeric(18,2);', ddl)
        self.assertNotIn('"pin"', ddl)

    def test_refusals(self):
        for d, target in (({"fields": []}, None), ({**good_definition(), "key": []}, None), (good_definition(), TARGET)):
            with self.assertRaises(HTTPException) as ctx:
                oca.build_proposal(d, target, "x")
            self.assertEqual(ctx.exception.status_code, 422)
        d = good_definition()
        d["fields"][0]["target_type"] = "OTHER"
        with self.assertRaises(HTTPException):
            oca.build_proposal(d, None, "x")


# ------------------------------------------------------------------ fake DB flow


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.calls.append((sql, params))
        if "set_config" in sql:
            self.rows = []
            return
        key = next(k for k in self.conn.handlers if k in sql)
        self.rows = self.conn.handlers[key](params)

    def fetchall(self):
        return self.rows


def snapshot_rows(snapshot):
    return [{"metadata_capture_id": snapshot["metadata_capture_id"], "finished_at": None, "column_name": c["name"],
             "ordinal_position": i, "formatted_type": c["formatted_type"], "is_nullable": c["nullable"],
             "is_primary_key": c["primary_key"]} for i, c in enumerate(snapshot["columns"], 1)]


def target_rows(target):
    return [{"schema_name": target["schema"], "table_name": target["table"], "column_name": c["name"], "attnum": i,
             "formatted_type": c["formatted_type"], "attnotnull": c["not_null"], "has_default": c["has_default"]}
            for i, c in enumerate(target["columns"], 1)]


class Conn:
    def __init__(self, cfg, snapshot=SNAPSHOT, target=TARGET):
        self.cfg = cfg
        self.calls = []
        self.handlers = {
            "integration_object_config_get": lambda p: [{"config": self.cfg}],
            "FROM meta.source_object_snapshot": lambda p: snapshot_rows(snapshot) if snapshot else [],
            "FROM pg_class": lambda p: target_rows(target) if target else [],
            "record_validation": lambda p: [{"id": 1}],
            "config_save_draft": lambda p: [{"revision": 2}],
            "config_publish": lambda p: [{"version_no": 1}],
            "set_drift": lambda p: [{"status": "DRIFT" if p[3] else "READY"}],
            "proposal_create": lambda p: [{"proposal_id": 5}],
            "proposal_reject": lambda p: [{"rejected": True}],
            "FROM meta.source_object": lambda p: [{"source_object_id": 16}],
        }

    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def sql_with(self, text):
        return [p for s, p in self.calls if text in s]


OBJECT = {"source_object_id": 16, "source_system_id": 5, "source_schema": "public", "source_object": "asan",
          "object_type": "TABLE", "is_active": True, "legacy": False}


def draft_cfg(definition=None, revision=2):
    return {"object": OBJECT, "current": None, "proposals": [],
            "draft": {"version_id": 3, "version_no": 1, "draft_revision": revision,
                      "definition": definition or good_definition()}}


class FlowTest(unittest.TestCase):
    TOKEN = "a" * 64

    def test_load(self):
        conn = Conn(draft_cfg())
        result = oca.load_object_config(conn, self.TOKEN, "req", 16)
        self.assertEqual(result.snapshot["columns"][1]["type"], "TEXT(20)")
        self.assertEqual(result.targets[0]["columns"][2]["type"], "TIMESTAMPTZ")
        self.assertEqual(result.compatibility["TEXT(20)|TEXT"], "SAFE")
        self.assertEqual(result.compatibility["BIGINT|DATE"], "INCOMPATIBLE")
        self.assertEqual(conn.calls[1][1], (self.TOKEN, 16))  # session checked first

    def test_save_draft_sends_document(self):
        conn = Conn(draft_cfg())
        payload = oca.DraftSaveRequest(expected_revision=1, config=oca.ObjectConfigDocument(**good_definition()))
        self.assertEqual(oca.save_draft(conn, self.TOKEN, "req", 16, payload).draft_revision, 2)
        (params,) = conn.sql_with("config_save_draft")
        self.assertEqual(params[:3], (self.TOKEN, 16, 1))
        self.assertEqual(params[3].obj["fields"][1]["conversion"], "SAFE")

    def test_validate_records_result(self):
        conn = Conn(draft_cfg())
        result = oca.validate_draft(conn, self.TOKEN, "req", 16, 2)
        self.assertTrue(result.ok)
        (params,) = conn.sql_with("record_validation")
        self.assertEqual(params[:4], (self.TOKEN, 16, 2, True))
        conn = Conn(draft_cfg(), target=None)
        self.assertFalse(oca.validate_draft(conn, self.TOKEN, "req", 16, 2).ok)

    def test_stale_revision(self):
        conn = Conn(draft_cfg(revision=3))
        with self.assertRaises(HTTPException) as ctx:
            oca.validate_draft(conn, self.TOKEN, "req", 16, 2)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(conn.sql_with("record_validation"), [])

    def test_publish_records_baseline(self):
        conn = Conn(draft_cfg())
        published = {"version_id": 3, "definition": good_definition(), "status_details": {}}

        def get_after_publish(p):
            return [{"config": {**draft_cfg(), "draft": None, "current": published}}] \
                if conn.sql_with("config_publish") else [{"config": draft_cfg()}]
        conn.handlers["integration_object_config_get"] = get_after_publish
        result = oca.publish_draft(conn, self.TOKEN, "req", 16, 2)
        self.assertEqual((result.version_no, result.status), (1, "READY"))
        (params,) = conn.sql_with("set_drift")
        self.assertEqual(params[2], 3)
        self.assertIs(params[3], False)
        self.assertEqual(params[4].obj["baseline"]["columns"]["pin"], "TEXT(20)")

    def test_publish_refused_when_outdated(self):
        conn = Conn(draft_cfg(), snapshot=None)
        with self.assertRaises(HTTPException) as ctx:
            oca.publish_draft(conn, self.TOKEN, "req", 16, 2)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(conn.sql_with("config_publish"), [])

    def test_drift_check(self):
        current = {"version_id": 3, "definition": good_definition(),
                   "status_details": {"baseline": oca.baseline_of(SNAPSHOT)}}
        cfg = {"object": OBJECT, "current": current, "draft": None, "proposals": []}
        changed = {**SNAPSHOT, "metadata_capture_id": 8, "columns": SNAPSHOT["columns"][:2]}
        conn = Conn(cfg, snapshot=changed)
        result = oca.check_drift(conn, self.TOKEN, "req", 16)
        self.assertEqual(result.status, "DRIFT")
        (params,) = conn.sql_with("set_drift")
        self.assertTrue(params[3])
        self.assertEqual(params[4].obj["baseline"]["metadata_capture_id"], 7)
        self.assertEqual(params[4].obj["checked_capture_id"], 8)
        self.assertEqual(oca.check_drift(Conn(cfg), self.TOKEN, "req", 16).status, "READY")

    def test_drift_after_snapshot_skips_unpublished(self):
        conn = Conn(draft_cfg())
        self.assertEqual(oca.drift_after_snapshot(conn, self.TOKEN, "req", 5), 0)
        self.assertEqual(conn.sql_with("set_drift"), [])

    def test_proposal(self):
        conn = Conn(draft_cfg(), target=None)
        result = oca.create_proposal(conn, self.TOKEN, "req", 16, 2)
        self.assertEqual((result.proposal_id, result.change_kind), (5, "CREATE_TABLE"))
        (params,) = conn.sql_with("proposal_create")
        self.assertEqual(params[:5], (self.TOKEN, 16, "CREATE_TABLE", "stg", "asan"))
        self.assertIn("public.asan", params[5])

    def test_db_errors_mapped(self):
        import psycopg

        class Diag:
            message_primary = "Draft not found or changed by another user"

        class Err(psycopg.Error):
            diag = Diag()

        conn = Conn(draft_cfg())

        def fail(p):
            raise Err("x")
        conn.handlers["config_save_draft"] = fail
        payload = oca.DraftSaveRequest(config=oca.ObjectConfigDocument())
        with self.assertRaises(HTTPException) as ctx:
            oca.save_draft(conn, self.TOKEN, "req", 16, payload)
        self.assertEqual(ctx.exception.status_code, 409)
        Diag.message_primary = "Legacy CRM sources keep their existing contracts and mappings"
        with self.assertRaises(HTTPException) as ctx:
            oca.save_draft(conn, self.TOKEN, "req", 16, payload)
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
