"""I02-03 step 5: tools/server/dwh_secret_accept.py and secret_read_probe.py without a server."""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
sys.path.insert(0, str(ROOT / "etl" / "src"))
import dwh_secret_accept as sa  # noqa: E402

REF = "11111111-1111-4111-8111-111111111111"


def ev(op, type_, phase, code, at):
    return {"op": op, "type": type_, "phase": phase, "code": code, "actor": "DWH_USER", "at": at}


def full_journal():
    events = []
    for i, t in enumerate(("CREATE", "READ", "REPLACE", "REVOKE")):
        at = f"2026-11-20T1{i}:00:00"
        events += [ev(f"op{i}", t, "ATTEMPT", "PERMITTED_ATTEMPT", at), ev(f"op{i}", t, "OUTCOME", "SUCCEEDED", at)]
    return {"read_only": "on", "found": True, "events": events, "bindings": [
        {"ref": REF, "state": "REVOKED", "sha": "x"}], "finished_request": 7}


def run_journal(data):
    report = sa.Report("JOURNAL")
    with redirect_stdout(io.StringIO()):
        sa.evaluate_journal(report, data)
    return report


class StreamTest(unittest.TestCase):
    def test_counts_across_chunk_borders_once(self):
        needle = b"DWHACCEPTabc"
        chunks = [b"xxDWHACC", b"EPTabcyyDWHACCEPTabc", b"DWHACCEPT", b"abc"]
        self.assertEqual(sa.count_in_stream(chunks, needle), (3, sum(map(len, chunks))))

    def test_gzip_file_is_read(self):
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "access.log.2.gz"
            with gzip.open(path, "wb") as h:
                h.write(b"a DWHACCEPT0 b")
            self.assertEqual(sa.count_in_stream(sa.file_chunks(path), b"DWHACCEPT0")[0], 1)


class MarkerTest(unittest.TestCase):
    def test_marker_is_private_and_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m"
            marker = sa.make_marker(path)
            self.assertRegex(marker, sa.MARKER_RE)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sa.read_marker(path), marker)
            with self.assertRaises(FileExistsError):
                sa.make_marker(path)
            self.assertNotEqual(sa.make_marker(path, force=True), marker)

    def test_foreign_file_is_not_a_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m"
            path.write_text("short")
            with self.assertRaises(ValueError):
                sa.read_marker(path)


class JournalTest(unittest.TestCase):
    def test_full_cycle_passes(self):
        self.assertEqual(run_journal(full_journal()).failed, 0)

    def test_read_after_revoke_fails(self):
        data = full_journal()
        data["events"] += [ev("late", "READ", "ATTEMPT", "PERMITTED_ATTEMPT", "2026-11-20T20:00:00"),
                           ev("late", "READ", "OUTCOME", "SUCCEEDED", "2026-11-20T20:00:00")]
        self.assertEqual(run_journal(data).failed, 1)

    def test_missing_outcome_missing_replace_and_active_binding_fail(self):
        data = full_journal()
        data["events"] = [e for e in data["events"] if not (e["op"] == "op2")]
        data["events"] = [e for e in data["events"] if not (e["op"] == "op3" and e["phase"] == "OUTCOME")]
        data["bindings"][0]["state"] = "ACTIVE"
        self.assertGreaterEqual(run_journal(data).failed, 4)

    def test_unknown_source(self):
        self.assertEqual(run_journal({"found": False, "events": [], "bindings": []}).failed, 1)


class EtlDenyTest(unittest.TestCase):
    def test_probe_refuses_and_never_prints_value(self):
        import secret_read_probe as probe
        from dwh.source_secret import SourceSecretError

        def reader(code, request_id=None):
            if request_id is None:
                raise SourceSecretError("The source password is read only for a load request")
            if request_id == -1:
                raise SourceSecretError(f"SecretStore READ refused for {code}, request -1: REQUEST_NOT_FOUND")
            return "secret-value"

        os.environ["DWH_RUN_OBJECT_REQUEST_ID"] = "5"
        results = dict(probe.probe("CRM_RETAIL", 7, reader=reader))
        self.assertNotIn("DWH_RUN_OBJECT_REQUEST_ID", os.environ)
        self.assertTrue(results["no_request"].startswith("REFUSED"))
        self.assertEqual(probe.refusal_code("read only for a load request (DWH_RUN_OBJECT_REQUEST_ID is not set)"),
                         "NO_LOAD_REQUEST")
        self.assertEqual(results["unknown_request"], "REFUSED REQUEST_NOT_FOUND")
        self.assertEqual(results["finished_request"], "GIVEN")
        self.assertNotIn("secret-value", repr(results))

    def test_evaluate_probe(self):
        good = ("PROBE no_request: REFUSED The source password\nPROBE unknown_request: REFUSED REQUEST_NOT_FOUND\n"
                "PROBE finished_request: REFUSED REQUEST_NOT_RUNNING")
        for output, before, after, failed in ((good, 3, 5, 0), (good, 3, 4, 1),
                                              (good.replace("REFUSED REQUEST_NOT_RUNNING", "GIVEN"), 3, 5, 1)):
            report = sa.Report("ETL_DENY")
            with redirect_stdout(io.StringIO()):
                sa.evaluate_probe(report, output, before, after)
            self.assertEqual(report.failed, failed)

    def test_probe_runs_in_a_unit_as_dwh(self):
        cmd = sa.probe_command("CRM_RETAIL", 7, root=Path("/opt/dwh/current"))
        self.assertEqual(cmd[-1], "/opt/dwh/current/src/tools/server/secret_read_probe.py")
        self.assertIn("--uid=dwh", cmd)
        self.assertIn("--setenv=DWH_PROBE_FINISHED_REQUEST=7", cmd)
        self.assertFalse(any("DWH_RUN_OBJECT_REQUEST_ID" in c for c in cmd))


class BackupTest(unittest.TestCase):
    def make_copy(self, tmp: Path, content: bytes, side_ok=True):
        (tmp / f"{REF}.bin").write_bytes(content)
        copy = tmp / "copies" / "secrets_20261120_130000.tar"
        copy.parent.mkdir()
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as h, tarfile.open(fileobj=h, mode="w") as t:
            t.add(tmp / f"{REF}.bin", arcname=f"{REF}.bin")
        digest = hashlib.sha256(copy.read_bytes()).hexdigest() if side_ok else "0" * 64
        (copy.parent / (copy.name + ".sha256")).write_text(
            f"{digest}  {copy.name}\n{hashlib.sha256(content).hexdigest()}  {REF}.bin\n")
        return copy.parent

    def run_backup(self, directory, active):
        report = sa.Report("BACKUP")
        with redirect_stdout(io.StringIO()):
            sa.evaluate_backup(report, directory, active)
        return report

    def test_copy_with_active_file_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.make_copy(Path(tmp), b"cipher")
            active = [{"ref": REF, "source": "CRM_RETAIL", "sha": hashlib.sha256(b"cipher").hexdigest().upper()}]
            self.assertEqual(self.run_backup(d, active).failed, 0)

    def test_missing_active_file_and_bad_sha_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.make_copy(Path(tmp), b"cipher", side_ok=False)
            active = [{"ref": "22222222-2222-4222-8222-222222222222", "source": "X", "sha": "ab"}]
            self.assertEqual(self.run_backup(d, active).failed, 2)

    def test_no_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_backup(Path(tmp), []).failed, 1)


if __name__ == "__main__":
    unittest.main()
