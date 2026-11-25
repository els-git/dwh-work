"""I12: files of a file source — settings, folder, listing, parsing, actuality date, choice of files (no database)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "etl" / "src"))

from dwh_api import file_gateway as fg  # noqa: E402
from dwh_api.file_gateway import FileSourceError  # noqa: E402

CFG = {"mask": "s_*.json", "format": "JSON", "records_path": "items", "date_path": "meta.actual_at"}


def cfg(**extra) -> dict:
    return fg.normalize({**CFG, **extra})


class NormalizeTest(unittest.TestCase):
    def test_defaults_and_refusals(self):
        c = cfg()
        self.assertEqual((c["mode"], c["time_zone"], c["wait_seconds"]), ("LATEST", "Asia/Baku", 60))
        for bad in ({"mask": "../x*"}, {"mask": "a/b"}, {"format": "CSV"}, {"mode": "ANY"}, {"date_path": ""},
                    {"time_zone": "Mars/Base"}, {"wait_seconds": 99999}, {"format": "XML", "records_path": ""}):
            with self.assertRaises(FileSourceError, msg=bad):
                cfg(**bad)
        draft = fg.normalize({"format": "XML"}, draft=True)
        self.assertEqual((draft["mask"], draft["date_path"]), ("*.xml", ""))
        self.assertEqual(cfg(format="JSONL")["records_path"], "")


class DateTest(unittest.TestCase):
    def test_formats_and_zone(self):
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(fg.parse_date("2026-11-25", "Asia/Baku"), utc(2026, 11, 24, 20, 0))
        self.assertEqual(fg.parse_date("2026-11-25T10:00:00Z", "Asia/Baku"), utc(2026, 11, 25, 10, 0))
        self.assertEqual(fg.parse_date("2026-11-25 10:00", "Asia/Baku"), utc(2026, 11, 25, 6, 0))
        self.assertEqual(fg.parse_date("25.11.2026 10:00:30", "Asia/Baku"), utc(2026, 11, 25, 6, 0, 30))
        for bad in (None, "", "yesterday", "2026-13-01", "31.02.2026", True, {"a": 1}, 20261125):
            self.assertIsNone(fg.parse_date(bad, "Asia/Baku"), bad)


class ParseTest(unittest.TestCase):
    def test_json(self):
        body = json.dumps({"meta": {"actual_at": "2026-11-25"}, "items": [{"id": 1, "a": {"b": 2}}, {"id": 2}]})
        p = fg.parse_file(body.encode(), cfg())
        self.assertEqual(len(p.records), 2)
        self.assertEqual(p.actual_at.isoformat(), "2026-11-24T20:00:00+00:00")
        self.assertIsNone(fg.parse_file(b'{"items": []}', cfg()).actual_at)
        for bad, code in ((b'{"items": [', "FILE_BROKEN"), (b'{"items": 5}', "FILE_BAD_RECORDS"),
                          (b"\xff\xfe", "FILE_BROKEN"), (("[" * 70 + "]" * 70).encode(), "FILE_BROKEN")):
            with self.assertRaises(FileSourceError) as ctx:
                fg.parse_file(bad, cfg())
            self.assertEqual(ctx.exception.code, code, bad[:20])

    def test_jsonl_date_in_first_line(self):
        body = b'{"id": 1, "actual_at": "2026-11-25T08:00:00+04:00"}\n\n{"id": 2}\n'
        p = fg.parse_file(body, cfg(format="JSONL", date_path="actual_at"))
        self.assertEqual(([r["id"] for r in p.records], p.actual_at.hour), ([1, 2], 4))
        with self.assertRaises(FileSourceError):
            fg.parse_file(b'{"id": 1}\nnot json\n', cfg(format="JSONL", date_path="actual_at"))

    def test_xml_records_date_and_dtd(self):
        x = cfg(format="XML", records_path="export.items.item", date_path="export.header.actual_at")
        body = (b"<export><header><actual_at>2026-11-25 09:00</actual_at></header><items>"
                b"<item><id>1</id><name>A</name></item><item><id>2</id><name null='true'/></item></items></export>")
        p = fg.parse_file(body, x)
        self.assertEqual(p.records, [{"id": "1", "name": "A"}, {"id": "2", "name": None}])
        self.assertEqual(p.actual_at.hour, 5)
        attr = fg.parse_file(b"<e><h><d value='2026-11-25'/></h><items><item><id>1</id></item></items></e>",
                             cfg(format="XML", records_path="items.item", date_path="h.d"))
        self.assertIsNotNone(attr.actual_at)
        bomb = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]><export>&a;</export>'
        with self.assertRaises(FileSourceError) as ctx:
            fg.parse_file(bomb, x)
        self.assertEqual(ctx.exception.code, "FILE_XML_DTD_FORBIDDEN")
        with self.assertRaises(FileSourceError) as ctx:
            fg.parse_file(b"<export><items>", x)
        self.assertEqual(ctx.exception.code, "FILE_BROKEN")


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "files"
        (self.root / "PV_FILES").mkdir(parents=True)
        (Path(self.tmp.name) / "secret.json").write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_of_the_source_only(self):
        self.assertEqual(fg.source_folder("PV_FILES", self.root), (self.root / "PV_FILES").resolve())
        for code, err in (("../x", "FILE_FOLDER_INVALID"), ("", "FILE_FOLDER_INVALID"), ("NONE", "FILE_FOLDER_MISSING")):
            with self.assertRaises(FileSourceError) as ctx:
                fg.source_folder(code, self.root)
            self.assertEqual(ctx.exception.code, err)

    def test_listing_mask_links_and_size(self):
        folder = fg.source_folder("PV_FILES", self.root)
        (folder / "s_1.json").write_text("{}")
        (folder / "s_2.json").write_text("{}")
        (folder / "S_3.json").write_text("{}")
        (folder / ".s_4.json").write_text("{}")
        (folder / "s_dir.json").mkdir()
        os.symlink(Path(self.tmp.name) / "secret.json", folder / "s_out.json")
        os.symlink(folder / "s_1.json", folder / "s_link.json")
        names = [e.name for e in fg.list_files(folder, "s_*.json")]
        self.assertEqual(names, ["s_1.json", "s_2.json", "s_link.json"])
        entry = fg.list_files(folder, "s_1.json")[0]
        body, sha = fg.read_file(entry)
        self.assertEqual((body, sha, fg.hash_file(entry)), (b"{}", sha, sha))
        with self.assertRaises(FileSourceError) as ctx:
            fg.read_file(entry, limit=1)
        self.assertEqual(ctx.exception.code, "FILE_TOO_LARGE")


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, query, args=()):
        if "object_file_state" in query:
            loaded = [v["actual_at"] for v in self.db.files.values() if v["status"] == "LOADED"]
            self.result = ({"last_loaded_at": max(loaded).isoformat() if loaded else None,
                            "known": {k: v["status"] for k, v in self.db.files.items()}},)
        elif "object_file_record" in query:
            run, name, size, sha, actual_at, status, rows, message = args
            self.db.files[sha] = {"name": name, "actual_at": actual_at, "status": status, "rows": rows,
                                  "message": message}
            self.result = (1,)
        elif "object_file_warning" in query:
            if self.db.without_035:
                import psycopg
                raise psycopg.errors.UndefinedFunction("function etl.object_file_warning does not exist")
            self.db.warnings.append((args[1], args[3]))
            self.result = (True,)

    def fetchone(self):
        return self.result


class FakeDwh:
    def __init__(self, without_035: bool = False):
        self.files: dict = {}
        self.warnings: list = []
        self.without_035 = without_035
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        self.rollbacks += 1


class ChooseFilesTest(unittest.TestCase):
    def setUp(self):
        import run_object

        self.ro = run_object
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "PV_FILES"
        self.folder.mkdir()
        self.plan = run_object.Plan("stg", "t", [
            {"target_column": "id", "source_column": "id", "source_type": "BIGINT", "target_type": "BIGINT",
             "write_policy": "AUTHORITATIVE"}], ["id"])

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, day, ids, *, old=True):
        doc = {"meta": {"actual_at": day} if day else {}, "items": [{"id": i} for i in ids]}
        path = self.folder / name
        path.write_text(json.dumps(doc))
        if old:
            os.utime(path, (time.time() - 3600, time.time() - 3600))

    def run_files(self, db, mode="LATEST"):
        applied = []

        def apply_chunks(chunks):
            for rows in chunks:
                applied.extend(r[0] for r in rows)

        n = self.ro.load_files(db, 1, cfg(mode=mode), "PV_FILES", self.plan, apply_chunks, lambda m: None,
                               root=Path(self.tmp.name))
        return n, applied

    def status(self, db):
        return sorted((v["name"], v["status"]) for v in db.files.values())

    def test_latest_loads_newest_only_then_only_newer(self):
        db = FakeDwh()
        self.write("s_1.json", "2026-11-20", [1])
        self.write("s_2.json", "2026-11-22", [2, 3])
        self.write("s_3.json", None, [4])
        self.write("s_4.json", "2026-11-22", [5])
        self.assertEqual(self.run_files(db), (1, ["2", "3"]))
        self.assertEqual(self.status(db), [("s_1.json", "NOT_ACTUAL"), ("s_2.json", "LOADED"),
                                           ("s_3.json", "NO_DATE"), ("s_4.json", "NOT_ACTUAL")])
        self.assertEqual(self.run_files(db), (0, []))  # nothing new: the register is not changed
        self.write("s_5.json", "2026-11-21", [6])
        self.write("s_6.json", "2026-11-23", [7])
        self.assertEqual(self.run_files(db), (1, ["7"]))
        self.assertIn(("s_5.json", "NOT_ACTUAL"), self.status(db))

    def test_all_new_in_date_order_and_waiting_for_a_file_being_written(self):
        db = FakeDwh()
        self.write("s_b.json", "2026-11-22", [2])
        self.write("s_a.json", "2026-11-21", [1])
        (self.folder / "s_c.json").write_text('{"items": [')  # just written, broken: waited for
        self.assertEqual(self.run_files(db, "ALL_NEW"), (2, ["1", "2"]))
        self.assertNotIn("s_c.json", [n for n, _ in self.status(db)])
        os.utime(self.folder / "s_c.json", (time.time() - 3600, time.time() - 3600))
        self.assertEqual(self.run_files(db, "ALL_NEW"), (0, []))
        self.assertIn(("s_c.json", "FAILED"), self.status(db))

    def test_failed_file_stops_the_run_and_is_retried(self):
        db = FakeDwh()
        (self.folder / "s_1.json").write_text(json.dumps({"meta": {"actual_at": "2026-11-22"}, "items": [{"x": 1}]}))
        with self.assertRaises(self.ro.RunError) as ctx:
            self.run_files(db)
        self.assertEqual(ctx.exception.code, "SOURCE_DRIFT")
        self.assertEqual(self.status(db), [("s_1.json", "FAILED")])
        self.write("s_1.json", "2026-11-22", [1])  # corrected file: new content
        self.assertEqual(self.run_files(db), (1, ["1"]))

    def test_refused_files_are_warnings_package_035(self):
        # I12 plan 7.7 (decision A): broken, too large and undated files are warnings; the run goes on
        db = FakeDwh()
        self.write("s_1.json", "2026-11-22", [1])
        self.write("s_2.json", None, [2])
        (self.folder / "s_3.json").write_text('{"items": [')
        os.utime(self.folder / "s_3.json", (time.time() - 3600, time.time() - 3600))
        self.assertEqual(self.run_files(db), (1, ["1"]))
        self.assertEqual(sorted(db.warnings), [("s_2.json", "FILE_NO_DATE"), ("s_3.json", "FILE_BROKEN")])

    def test_without_package_035_the_load_goes_on(self):
        db = FakeDwh(without_035=True)
        self.write("s_1.json", "2026-11-22", [1])
        self.write("s_2.json", None, [2])
        logs = []
        n = self.ro.load_files(db, 1, cfg(), "PV_FILES", self.plan, lambda c: None, logs.append,
                               root=Path(self.tmp.name))
        self.assertEqual(n, 1)
        self.assertEqual(db.rollbacks, 1)
        self.assertTrue(any("package 035" in m for m in logs), logs)

    def test_missing_folder(self):
        with self.assertRaises(FileSourceError) as ctx:
            self.ro.load_files(FakeDwh(), 1, cfg(), "OTHER", self.plan, lambda c: None, lambda m: None,
                               root=Path(self.tmp.name))
        self.assertEqual(ctx.exception.code, "FILE_FOLDER_MISSING")


class ReadOnlyGuardTest(unittest.TestCase):
    def test_file_code_never_writes_files(self):
        """I12 (F1): the file gateway and the FILE branch of the engine only read files."""
        import ast

        for path in (ROOT / "backend/src/dwh_api/file_gateway.py", ROOT / "etl/src/run_object.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    owner = getattr(getattr(node.func, "value", None), "id", "")
                    self.assertNotIn(name, {"unlink", "rename", "rmtree", "write_text", "write_bytes", "chmod",
                                            "mkdir", "touch"}, f"{path.name}: {name}")
                    if owner in ("os", "shutil"):
                        self.assertNotIn(name, {"remove", "replace", "truncate", "move", "makedirs", "rmdir",
                                                "symlink", "link"}, f"{path.name}: {owner}.{name}")
                    if name == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        self.assertNotRegex(str(node.args[1].value), "[wax+]", f"{path.name}: open mode")


if __name__ == "__main__":
    unittest.main()
