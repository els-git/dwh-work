"""I12-07: tools/server/dwh_test_files.py - test files of PV_FILES (no network: the test API answer is faked)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "tools" / "server"))

import dwh_test_files as tool  # noqa: E402
from dwh_api import file_gateway as fg  # noqa: E402

ROWS = [{"id": i, "code": f"S{i}", "description": f"d{i}", "variables": {"a": i}} for i in range(1, 8)]


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(seen):
    def open_(request, timeout=None):
        seen.append(request)
        return Answer(json.dumps({"data": {"items": ROWS}}).encode())
    return open_


class TestFilesTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = self.root / "test-api.env"
        self.env.write_text("PV_API_TOKEN=" + "t" * 40 + "\nPV_API_PORT=8090\n")
        self.today = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *argv, env=None):
        seen = []
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(list(argv), root=self.root, env_file=env or self.env, opener=opener(seen),
                             today=self.today)
        return code, out.getvalue(), seen

    def test_plan_changes_nothing(self):
        code, out, seen = self.run_tool()
        self.assertEqual((code, seen), (0, []))
        self.assertIn("TEST_FILES_RESULT: PLAN", out)
        self.assertFalse((self.root / "PV_FILES").exists())

    def test_apply_writes_readable_files_with_dates_inside(self):
        code, out, seen = self.run_tool("--apply")
        self.assertEqual(code, 0, out)
        self.assertNotIn("t" * 40, out)
        self.assertEqual(seen[0].full_url, "http://127.0.0.1:8090/v1/services?page=1&size=500")
        folder = self.root / "PV_FILES"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(
            ["svc_full_1.json", "svc_full_2.json", "svc_full_3.json", "svc_inc_1.json", "svc_inc_2.json",
             "svc_inc_3.json", "svc_xml_1.xml", "svc_xml_2.xml", "svc_jsonl_1.jsonl"]))
        cfg = fg.normalize({"mask": "*", "format": "JSON", "records_path": "data.items", "date_path": "meta.actual_at"})
        parsed = [fg.parse_file((folder / f"svc_inc_{n}.json").read_bytes(), cfg) for n in (1, 2, 3)]
        self.assertEqual([p.actual_at.isoformat() for p in parsed],
                         ["2026-11-23T05:00:00+00:00", "2026-11-24T05:00:00+00:00", "2026-11-25T05:00:00+00:00"])
        self.assertEqual([sum("(file v" in r["description"] for r in p.records) for p in parsed], [0, 3, 6])
        x = fg.parse_file((folder / "svc_xml_2.xml").read_bytes(), fg.normalize(
            {"mask": "*", "format": "XML", "records_path": "export.items.service", "date_path": "export.header.actual_at"}))
        jl = fg.parse_file((folder / "svc_jsonl_1.jsonl").read_bytes(), fg.normalize(
            {"mask": "*", "format": "JSONL", "date_path": "actual_at"}))
        self.assertEqual((len(x.records), len(jl.records)), (7, 7))
        self.assertEqual(x.actual_at, jl.actual_at)
        self.assertEqual(oct((folder / "svc_inc_1.json").stat().st_mode & 0o777), "0o640")

    def test_adds_and_remove(self):
        self.run_tool("--apply")
        folder = self.root / "PV_FILES"
        for name in tool.ADDS:
            code, out, _ = self.run_tool("--add", name)
            self.assertEqual(code, 0, (name, out))
        self.assertEqual((folder / "svc_inc_copy.json").read_bytes(), (folder / "svc_inc_3.json").read_bytes())
        self.assertGreater((folder / "svc_inc_big.json").stat().st_size, fg.DEFAULT_MAX_BYTES)
        with self.assertRaises(fg.FileSourceError) as ctx:
            fg.parse_file((folder / "svc_xml_dtd.xml").read_bytes(), fg.normalize(
                {"mask": "*", "format": "XML", "records_path": "export.items.service", "date_path": "export.header.actual_at"}))
        self.assertEqual(ctx.exception.code, "FILE_XML_DTD_FORBIDDEN")
        code, out, _ = self.run_tool("--remove")
        self.assertEqual((code, list(folder.iterdir())), (0, []))

    def test_without_the_test_api_service(self):
        code, out, _ = self.run_tool("--apply", env=self.root / "missing.env")
        self.assertEqual(code, 1)
        self.assertIn("TEST_FILES_RESULT: FAIL", out)


if __name__ == "__main__":
    unittest.main()
