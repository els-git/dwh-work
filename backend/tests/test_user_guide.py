"""I17 step 4: every section of docs/DWH_USER_GUIDE.md carries a valid access marker (the assistant
filters help by the user's role), and the guide names only known permission codes."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "DWH_USER_GUIDE.md"
KNOWN = {
    "NAV_INTEGRATION_VIEW", "INTEGRATION_VIEW", "NAV_ETL_VIEW", "ETL_VIEW", "NAV_DQ_VIEW", "DQ_VIEW",
    "NAV_REPORTING_VIEW", "REPORT_VIEW", "ADMIN_MODULE_MANAGE", "ADMIN_USER_MANAGE", "ADMIN_ROLE_MANAGE",
    "ADMIN_ACCESS_MANAGE", "ADMIN_INTEGRATION_MANAGE", "AI_ASSISTANT_USE", "AI_REPORT_RUN", "AI_ADMIN_MANAGE",
}
MARKER = re.compile(r"^<!-- access: (.+) -->$")


def parse(value: str) -> dict:
    if value == "all":
        return {}
    rules = {}
    for part in value.split(";"):
        key, _, items = part.strip().partition("=")
        rules[key] = items.split(",")
    return rules


class UserGuideTests(unittest.TestCase):
    def setUp(self):
        self.lines = GUIDE.read_text(encoding="utf-8").splitlines()

    def test_every_top_section_has_marker(self):
        for i, line in enumerate(self.lines):
            if line.startswith("## "):
                self.assertRegex(self.lines[i + 1], MARKER, line)

    def test_markers_are_valid(self):
        markers = [MARKER.match(line).group(1) for line in self.lines if MARKER.match(line)]
        self.assertGreaterEqual(len(markers), 15)
        for value in markers:
            rules = parse(value)
            self.assertTrue(set(rules) <= {"permissions", "level", "resource"}, value)
            for code in rules.get("permissions", []):
                self.assertIn(code, KNOWN, value)
            for level in rules.get("level", []):
                self.assertIn(level, ("ADMIN", "SUPERADMIN"), value)
            for resource in rules.get("resource", []):
                self.assertEqual(resource, "MODULE:CDP", value)

    def test_marker_follows_its_heading(self):
        for i, line in enumerate(self.lines):
            if MARKER.match(line):
                self.assertTrue(self.lines[i - 1].startswith("#"), f"line {i + 1}")


if __name__ == "__main__":
    unittest.main()
