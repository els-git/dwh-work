"""I09: schedules of objects — API model validation, error mapping and the calls to the database functions."""

import os
import unittest
from unittest import mock

os.environ.setdefault("DWH_DB_NAME", "unit-test-not-used")
os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

import psycopg
from fastapi import HTTPException
from pydantic import ValidationError

from dwh_api import object_schedule_admin as api


class FakeDbError(psycopg.Error):
    pass


class ModelTest(unittest.TestCase):
    def test_valid_and_defaults(self):
        m = api.ObjectScheduleSave(enabled=True, frequency="DAILY", at_time="06:30")
        self.assertEqual((m.run_kind, m.retry_count, m.retry_delay_minutes), ("RUN", 0, 15))

    def test_rejected_values(self):
        for bad in ({"frequency": "HOURLY"}, {"frequency": "DAILY", "at_time": "6:30"},
                    {"frequency": "DAILY", "at_time": "24:00"}, {"frequency": "EVERY_MINUTES", "every_n": 0},
                    {"frequency": "DAILY", "at_time": "06:30", "run_kind": "CONTINUE_ODS"},
                    {"frequency": "DAILY", "at_time": "06:30", "retry_count": 4},
                    {"frequency": "DAILY", "at_time": "06:30", "retry_delay_minutes": 2}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                api.ObjectScheduleSave(enabled=True, **bad)


class CallTest(unittest.TestCase):
    def test_save_passes_all_fields(self):
        payload = api.ObjectScheduleSave(enabled=True, frequency="WEEKLY", at_time="07:15", week_days=[1, 5],
                                         run_kind="FULL_RERUN", retry_count=2, retry_delay_minutes=30)
        with mock.patch.object(api.object_run_admin, "_call", return_value={"schedule_id": 3}) as call:
            self.assertEqual(api.save_object_schedule(payload, request=mock.Mock(), source_object_id=24),
                             {"schedule_id": 3})
        sql, params = call.call_args.args[1], call.call_args.args[2]
        self.assertIn("app.object_schedule_save", sql)
        self.assertEqual(params, (24, True, "WEEKLY", None, "07:15", [1, 5], "FULL_RERUN", 2, 30))
        self.assertIs(call.call_args.kwargs["raise_error"], api._raise_schedule_error)

    def test_list(self):
        with mock.patch.object(api.object_run_admin, "_call", return_value={"objects": []}) as call:
            self.assertEqual(api.list_object_schedules(mock.Mock()), {"objects": []})
        self.assertIn("app.object_schedule_list", call.call_args.args[1])

    def test_schedule_errors_are_422(self):
        for message in ("Invalid schedule: minutes must be from 15 to 720",
                        "Object has no published READY configuration or is not active", "Source object not found"):
            with mock.patch.object(api, "_db_message", return_value=message):
                with self.assertRaises(HTTPException) as ctx:
                    api._raise_schedule_error(FakeDbError(message))
            self.assertEqual(ctx.exception.status_code, 422)

    def test_other_errors_go_to_common_handler(self):
        with mock.patch.object(api, "_db_message", return_value="Integration run access denied"), \
                mock.patch.object(api, "_raise_database_error", side_effect=HTTPException(status_code=403)) as common:
            with self.assertRaises(HTTPException) as ctx:
                api._raise_schedule_error(FakeDbError("x"))
        self.assertEqual(ctx.exception.status_code, 403)
        common.assert_called_once()


class RouteTest(unittest.TestCase):
    def test_routes_registered(self):
        from fastapi.testclient import TestClient
        from dwh_api.main import app
        client = TestClient(app)
        # without a session the middleware refuses; the route exists (not 404)
        self.assertNotEqual(client.get("/api/admin/integrations/object-schedules").status_code, 404)
        self.assertNotEqual(client.put("/api/admin/integrations/object-schedules/1", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
