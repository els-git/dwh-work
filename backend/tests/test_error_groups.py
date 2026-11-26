"""I13-02: every error code raised by the engines has a group (backend/src/dwh_api/error_groups.py)."""
import re
import unittest
from pathlib import Path

from dwh_api import error_groups
from dwh_api.ai_diagnosis import KNOWLEDGE

REPO = Path(__file__).resolve().parents[2]
# files whose quoted UPPER_CASE words that look like codes are raised as errors of loads and marts
SOURCES = {
    "backend/src/dwh_api/http_gateway.py": r'"(API_[A-Z_]+)"',
    "backend/src/dwh_api/api_request.py": r'"((?:API|CURL)_[A-Z_]+)"',
    "backend/src/dwh_api/file_gateway.py": r'"(FILE_[A-Z_]+)"',
    "backend/src/dwh_api/source_gateway.py": r'"(SOURCE_[A-Z_]+)"',
    "etl/src/dm_mart_worker.py": r'"(DM_[A-Z_]+|INTERNAL_ERROR)"',
    "etl/src/run_object.py": r'RunError\(\s*"([A-Z_]+)"',
}


class ErrorGroupTests(unittest.TestCase):
    def test_every_engine_code_has_a_group(self):
        missing = []
        for rel, pattern in SOURCES.items():
            for code in set(re.findall(pattern, (REPO / rel).read_text(encoding="utf-8"))):
                if error_groups.group_of(code) == "OTHER":
                    missing.append(f"{rel}: {code}")
        self.assertEqual([], sorted(missing))

    def test_every_code_of_the_knowledge_base_has_a_group(self):
        self.assertEqual([], sorted(c for c in KNOWLEDGE if error_groups.group_of(c) == "OTHER"))

    def test_groups_and_prefixes(self):
        self.assertEqual("CONNECTION", error_groups.group_of("HTTP_429"))
        self.assertEqual("KEY", error_groups.group_of("key_null"))
        self.assertEqual("OTHER", error_groups.group_of(None))
        self.assertEqual("OTHER", error_groups.group_of("SOMETHING_OLD"))
        self.assertTrue(set(error_groups.CODES.values()) <= set(error_groups.GROUPS))

    def test_rows_get_a_group(self):
        rows = error_groups.with_groups([{"error_code": "SCHEMA_DRIFT"}, {"error_code": None}])
        self.assertEqual(["STRUCTURE", "OTHER"], [r["error_group"] for r in rows])


if __name__ == "__main__":
    unittest.main()
