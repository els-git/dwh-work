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


class DispatcherQueueTests(unittest.TestCase):
    def run_main(self, objects: bool, marts: bool) -> list[str]:
        calls: list[str] = []
        with mock.patch.object(dispatcher, "get_dwh_config"), \
                mock.patch.object(dispatcher, "connect", return_value=FakeConn()), \
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
        self.assertEqual(self.run_main(True, True), ["object"])

    def test_mart_when_no_object(self):
        self.assertEqual(self.run_main(False, True), ["object", "mart"])

    def test_nothing_queued(self):
        self.assertEqual(self.run_main(False, False), ["object", "mart", "print:NO QUEUED REQUEST"])

    def test_old_crm_queue_removed(self):
        for name in ("claim_request", "execute_task", "ALLOWED_TASKS", "RunRequest"):
            self.assertFalse(hasattr(dispatcher, name), name)


if __name__ == "__main__":
    unittest.main()
