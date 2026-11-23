"""I18: the dispatcher takes an object run request, otherwise a DM mart request; the old CRM queue is gone."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "src"))

import run_request_dispatcher as dispatcher  # noqa: E402


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        pass

    def fetchone(self):
        return ("dwh", "dwh_etl")


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor()


class ScheduleCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)

    def fetchone(self):
        value = self.conn.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ScheduleConn:
    def __init__(self, results):
        self.results = list(results)
        self.sql: list[str] = []
        self.done: list[str] = []

    def cursor(self):
        return ScheduleCursor(self)

    def commit(self):
        self.done.append("commit")

    def rollback(self):
        self.done.append("rollback")


class DispatcherQueueTests(unittest.TestCase):
    def run_main(self, objects: bool, marts: bool) -> list[str]:
        calls: list[str] = []
        with mock.patch.object(dispatcher, "get_dwh_config"), \
                mock.patch.object(dispatcher, "connect", return_value=FakeConn()), \
                mock.patch.object(dispatcher, "fire_schedules",
                                  side_effect=lambda c, n: calls.append("schedules") or 0), \
                mock.patch.object(dispatcher, "process_object_request",
                                  side_effect=lambda c, n: calls.append("object") or objects), \
                mock.patch.object(dispatcher, "process_mart_request",
                                  side_effect=lambda c, n: calls.append("mart") or marts), \
                mock.patch("builtins.print") as printed:
            dispatcher.main()
        if printed.call_args_list:
            calls.append("print:" + " ".join(str(a) for a in printed.call_args_list[-1].args))
        return calls

    def test_object_first(self):
        self.assertEqual(self.run_main(True, True), ["schedules", "object"])

    def test_mart_when_no_object(self):
        self.assertEqual(self.run_main(False, True), ["schedules", "object", "mart"])

    def test_nothing_queued(self):
        self.assertEqual(self.run_main(False, False), ["schedules", "object", "mart", "print:NO QUEUED REQUEST"])

    def test_schedules_without_package_032(self):
        conn = ScheduleConn([(False,)])
        self.assertEqual(dispatcher.fire_schedules(conn, "w"), 0)
        self.assertEqual(conn.sql, ["SELECT to_regprocedure('etl.object_schedule_fire(text)') IS NOT NULL"])
        self.assertEqual(conn.done, ["commit"])

    def test_schedules_fire(self):
        conn = ScheduleConn([(True,), (2,)])
        with mock.patch("builtins.print") as printed:
            self.assertEqual(dispatcher.fire_schedules(conn, "w"), 2)
        self.assertEqual(conn.sql[1], "SELECT etl.object_schedule_fire(%s)")
        self.assertEqual(conn.done, ["commit"])
        self.assertIn("SCHEDULED REQUESTS CREATED: 2", printed.call_args.args[0])

    def test_schedules_failure_does_not_stop(self):
        conn = ScheduleConn([(True,), RuntimeError("boom")])
        with mock.patch("builtins.print") as printed:
            self.assertEqual(dispatcher.fire_schedules(conn, "w"), 0)
            self.assertEqual(dispatcher.fire_schedules(ScheduleConn([(True,), RuntimeError("boom")]), "w"), 0)
        self.assertEqual(conn.done, ["rollback"])
        self.assertEqual(printed.call_count, 1)
        dispatcher.fire_schedules(ScheduleConn([(False,)]), "w")
        self.assertFalse(dispatcher._SCHEDULE_WARNED)

    def test_old_crm_queue_removed(self):
        for name in ("claim_request", "execute_task", "ALLOWED_TASKS", "RunRequest"):
            self.assertFalse(hasattr(dispatcher, name), name)


if __name__ == "__main__":
    unittest.main()
