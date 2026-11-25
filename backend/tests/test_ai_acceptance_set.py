"""I17 step 7: the acceptance set is consistent with the application (guide sections, SQL checks, roles)."""
from __future__ import annotations

import datetime
import decimal
import json
import sys
import unittest
from pathlib import Path

from dwh_api.ai_core import HELP_CONTEXT_CHARS, trim_found
from dwh_api.ai_knowledge import KnowledgeIndex, UserScope, is_russian
from dwh_api.ai_sql_guard import AllowedObject, check_text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
import dwh_ai_acceptance as acc  # noqa: E402

DOC = json.loads((ROOT / "tools" / "server" / "ai_acceptance.json").read_text(encoding="utf-8"))
CUR = AllowedObject(1, "dm", "v_retail_application_current")
DM = {"v_retail_application_current", "retail_application_history", "v_retail_application_source"}


class AcceptanceSetTests(unittest.TestCase):
    def test_size_and_ids(self):
        ids = [i["id"] for i in DOC["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        ok_help = [i for i in DOC["items"] if i["kind"] == "help" and i["expect"] == "OK" and i["role"] == "admin"]
        ok_report = [i for i in DOC["items"] if i["kind"] == "report" and i["expect"] == "OK"]
        self.assertGreaterEqual(len(ok_help), 30)
        self.assertGreaterEqual(len(ok_report), 20)
        self.assertTrue(any(i["role"] == "audit" for i in DOC["items"]))

    def test_expected_sections_exist_and_are_found(self):
        index = KnowledgeIndex.from_file()
        numbers = {s.number for s in index.sections}
        for item in DOC["items"]:
            if item["kind"] != "help":
                continue
            if not is_russian(item["question"]):
                continue  # translated by the model first (dwh_ai_acceptance.py), not searchable offline
            role = DOC["roles"][item["role"]]
            user = UserScope(role["level"], frozenset(role["permissions"]), frozenset(role["resources"]))
            found = index.search(item["question"], user)
            if item["expect"] == "DENIED":
                self.assertTrue(found.only_denied, item["id"])
                continue
            for number in item["sections"]:
                self.assertIn(number, numbers, item["id"])
            self.assertTrue(any(s.number in item["sections"] for s in found.sections), item["id"])
            # 25.11.2026: the model gets only the matching lines; an expected section must survive the cut
            trimmed = trim_found(found, item["question"])
            self.assertTrue(any(s.number in item["sections"] for s in trimmed.sections), item["id"])
            self.assertLessEqual(sum(len(s.text) for s in trimmed.sections), HELP_CONTEXT_CHARS, item["id"])

    def test_reference_sql_passes_the_application_check(self):
        for item in DOC["items"]:
            if item.get("reference_sql"):
                statement, ids = check_text(item["reference_sql"], [CUR], DM)
                self.assertEqual(ids, [1], item["id"])

    def test_row_comparison(self):
        a = [{"x": "IB", "n": 15}, {"x": "BR", "n": decimal.Decimal("3.000")}]
        b = [{"n": 3.0, "label": "BR"}, {"c": 15, "k": "IB"}]
        self.assertEqual(acc.rows_key(a), acc.rows_key(b))
        self.assertNotEqual(acc.rows_key(a), acc.rows_key([{"x": "IB", "n": 16}, {"x": "BR", "n": 3}]))
        self.assertEqual(acc.norm(datetime.date(2026, 11, 1)), "2026-11-01")
        self.assertEqual(acc.norm(None), "")


if __name__ == "__main__":
    unittest.main()
