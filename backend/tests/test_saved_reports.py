"""I17-09 step 3: API of saved reports; rights are decided by the database (package 017), the API maps its answers."""
from __future__ import annotations

import unittest

import psycopg
from fastapi import HTTPException

from dwh_api import saved_reports


class FakeError(psycopg.Error):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SavedReportsApiTests(unittest.TestCase):
    def test_routes(self):
        paths = {(route.path, tuple(sorted(route.methods))) for route in saved_reports.router.routes}
        for path, method in (("/api/reports/saved/folders", "GET"), ("/api/reports/saved/folders", "POST"),
                             ("/api/reports/saved/folders/{folder_id}/delete", "POST"),
                             ("/api/reports/saved/reports", "GET"), ("/api/reports/saved/reports", "POST"),
                             ("/api/reports/saved/reports/{report_id}/run", "POST"),
                             ("/api/reports/saved/reports/{report_id}/delete", "POST")):
            self.assertIn((path, (method,)), paths)

    def test_database_answers_become_http_codes(self):
        original = saved_reports._db_message
        saved_reports._db_message = lambda exc: exc.message
        try:
            for message, code in (("Access denied: AI_REPORT_RUN", 403),
                                  ("Only the owner or an administrator may delete this report", 403),
                                  ("Report not found", 404), ("Folder not found", 404),
                                  ("Folder is not empty", 409), ("Only an own successful report can be saved", 409),
                                  ('duplicate key value violates unique constraint "report_folder_shared_name"', 409)):
                with self.assertRaises(HTTPException) as ctx:
                    saved_reports._raise(FakeError(message))
                self.assertEqual(ctx.exception.status_code, code, message)
        finally:
            saved_reports._db_message = original


if __name__ == "__main__":
    unittest.main()
