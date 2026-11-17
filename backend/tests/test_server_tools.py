"""I16 step 3: server tools - release package, installer with rollback, backup, ETL tasks, settings."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[2]
for _path in (PROJECT / "tools" / "server", PROJECT / "tools" / "release"):
    sys.path.insert(0, str(_path))

import dispatcher_service  # noqa: E402
import dwh_backup  # noqa: E402
import dwh_prepare_server as prep  # noqa: E402
import dwh_import_db as imp  # noqa: E402
import dwh_sql_package as sqlpkg  # noqa: E402
import dwh_ai_setup as aisetup  # noqa: E402
import dwh_task  # noqa: E402
import dwh_update as up  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("DWH_I02_TEST_TEMP") or None)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


def make_package(directory: Path, version="2026.11.1", files=None, tamper=None, bad_name=None):
    files = files if files is not None else {"dwh_update.py": b"print()\n", "wheels/x.whl": b"wheel"}
    manifest = {"format": up.FORMAT, "version": version, "commit": "a" * 40,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    package = directory / f"dwh_release_{version}.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, tamper if tamper and name == "wheels/x.whl" else data)
        if bad_name:
            archive.writestr(bad_name, b"x")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix(".sha256").write_text(f"{digest}  {package.name}\n")
    return package


class PackageCheckTests(Temp):
    def test_good_package(self):
        package = make_package(self.dir)
        up.check_package_checksum(package)
        staging = self.dir / "staging"
        staging.mkdir()
        manifest = up.unpack(package, staging)
        self.assertEqual(manifest["version"], "2026.11.1")

    def test_refusals(self):
        package = make_package(self.dir)
        package.with_suffix(".sha256").write_text("0" * 64 + f"  {package.name}\n")
        with self.assertRaisesRegex(up.UpdateError, "does not match"):
            up.check_package_checksum(package)
        package.with_suffix(".sha256").unlink()
        with self.assertRaisesRegex(up.UpdateError, "not found"):
            up.check_package_checksum(package)
        cases = {"tampered": dict(tamper=b"evil"), "extra file": dict(bad_name="extra.txt"),
                 "traversal": dict(bad_name="../escape.txt")}
        for name, kwargs in cases.items():
            with self.subTest(name):
                sub = self.dir / name.replace(" ", "_")
                sub.mkdir()
                pkg = make_package(sub, **kwargs)
                staging = sub / "staging"
                staging.mkdir()
                with self.assertRaises(up.UpdateError):
                    up.unpack(pkg, staging)
        self.assertFalse((self.dir / "escape.txt").exists())


class FakeServices:
    """systemctl/runuser stand-in: records calls; a release is 'healthy' unless marked broken."""

    def __init__(self, settings_holder, broken=()):
        self.calls = []
        self.holder = settings_holder
        self.broken = set(broken)

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "systemctl" and cmd[1] == "is-active":
            current = os.path.realpath(self.holder.current)
            return subprocess.CompletedProcess(cmd, 1 if Path(current).name in self.broken else 0, "", "")
        if cmd[0] in ("systemctl", "runuser"):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return up.run_command(cmd, **kwargs)


class SwitchTests(Temp):
    def settings(self, broken=()):
        s = up.Settings(root=self.dir / "opt", state=self.dir / "state", unit_dir=self.dir / "units",
                        health_url="http://127.0.0.1:9/", health_seconds=0, log=lambda m: None)
        s.run = FakeServices(s, broken)
        s.releases.mkdir(parents=True)
        s.unit_dir.mkdir()
        return s

    def release(self, s, name, unit_text="[Service]\n"):
        path = s.releases / name
        (path / "src" / "deploy" / "systemd").mkdir(parents=True)
        (path / "src" / "deploy" / "systemd" / "dwh-api.service").write_text(unit_text)
        return path

    def test_switch_installs_units_and_rolls_back(self):
        s = self.settings(broken={"v2"})
        v1, v2 = self.release(s, "v1"), self.release(s, "v2", "[Service]\nX=2\n")
        with mock.patch.object(up, "healthy", lambda st: (os.path.realpath(st.current).endswith("v1"), "fake")):
            self.assertTrue(up.install_units(s, v1))
            self.assertEqual(up.restart_on(s, v1)[0], True)
            self.assertFalse(up.install_units(s, v1))  # unchanged: no daemon-reload
            self.assertEqual(Path(os.path.realpath(s.current)), v1.resolve())
            ok, _ = up.restart_on(s, v2)
            self.assertFalse(ok)
            up.install_units(s, v1)
            self.assertTrue(up.restart_on(s, v1)[0])
        self.assertEqual(Path(os.path.realpath(s.current)), v1.resolve())
        self.assertEqual((s.unit_dir / "dwh-api.service").read_text(), "[Service]\n")
        stops = [c for c in s.run.calls if c[:2] == ["systemctl", "stop"]]
        self.assertEqual(stops[0][2], "dwh-dispatcher.service")  # dispatcher stops first

    def test_manual_rollback(self):
        s = self.settings()
        v1, v2 = self.release(s, "v1"), self.release(s, "v2", "[Service]\nX=2\n")
        for r in (v1, v2):
            (r / "venv").mkdir()
        up.switch_to(s, v2)
        with mock.patch.object(up, "healthy", lambda st: (True, "fake")):
            self.assertEqual(up.rollback("v1", s), 0)
            self.assertEqual(Path(os.path.realpath(s.current)), v1.resolve())
            self.assertEqual((s.unit_dir / "dwh-api.service").read_text(), "[Service]\n")
            with self.assertRaises(up.UpdateError):
                up.rollback("v1", s)  # already current
            with self.assertRaises(up.UpdateError):
                up.rollback("v9", s)  # not installed
        with mock.patch.object(up, "healthy", lambda st: (os.path.realpath(st.current).endswith("v1"), "fake")):
            self.assertEqual(up.rollback("v2", s), 1)  # v2 does not start: back to v1
        self.assertEqual(Path(os.path.realpath(s.current)), v1.resolve())

    def test_prune_keeps_current_and_previous(self):
        s = self.settings()
        s.keep = 1
        paths = [self.release(s, f"v{i}") for i in range(4)]
        for i, path in enumerate(paths):
            os.utime(path, (1000 + i, 1000 + i))
        removed = up.prune(s, {paths[0].resolve(), paths[3].resolve()})
        self.assertEqual(sorted(p.name for p in removed), ["v1", "v2"])


class BackupTests(Temp):
    def test_copy_name_check_and_retention(self):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "pg_dump":
                Path(cmd[cmd.index("-f") + 1]).write_bytes(b"PGDMP")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        import datetime
        for day in range(1, 5):
            dwh_backup.make_copy(self.dir, "dwh", None, datetime.datetime(2026, 11, day, 2, 30), runner)
        target = dwh_backup.make_copy(self.dir, "dwh", "pre-update-2026.11.1",
                                      datetime.datetime(2026, 11, 5, 9, 0), runner)
        self.assertTrue(target.name.endswith("_pre-update-2026.11.1.dump"))
        self.assertIn(["pg_restore", "--list", str(target) + ".partial"], calls)
        removed = dwh_backup.prune(self.dir, 3)
        self.assertEqual(len(removed), 2)
        left = sorted(p.name for p in self.dir.glob("*.dump"))
        self.assertEqual(left[0], "dwh_20261103_023000.dump")
        self.assertEqual(len(list(self.dir.glob("*.sha256"))), 3)
        with self.assertRaises(ValueError):
            dwh_backup.make_copy(self.dir, "dwh", "../x", runner=runner)


class TaskCommandTests(Temp):
    def test_unit_carries_settings_keys_and_object(self):
        dropin = self.dir / "10-keyring.conf"
        dropin.write_text("[Service]\nEnvironmentFile=/etc/dwh/keyring.env\n"
                          "LoadCredentialEncrypted=dwh-key.K2026_04:/etc/dwh/keyring/K2026_04.cred\n"
                          "LoadCredentialEncrypted=evil:/tmp/x\n")
        keyring_env = self.dir / "keyring.env"
        keyring_env.write_text("DWH_SECRET_KEY_PROVIDER=systemd-creds\n")
        cmd = dwh_task.command("run_object.py", env={"DWH_RUN_OBJECT_ID": "16"}, dropin=dropin,
                               keyring_env=keyring_env)
        self.assertEqual(cmd[:3], ["systemd-run", "--wait", "--pipe"])
        self.assertIn("--uid=dwh", cmd)
        self.assertIn("--property=LoadCredentialEncrypted=dwh-key.K2026_04:/etc/dwh/keyring/K2026_04.cred", cmd)
        self.assertFalse(any("evil" in part for part in cmd))
        self.assertIn(f"--property=EnvironmentFile={keyring_env}", cmd)
        self.assertIn("--setenv=DWH_RUN_OBJECT_ID=16", cmd)
        self.assertEqual(cmd[-1], "/opt/dwh/current/src/etl/src/run_object.py")
        self.assertEqual(dwh_task.main(["run-object"]), 2)
        self.assertEqual(dwh_task.main(["plan-snapshot", "--snapshot-date", "4.11.2026"]), 2)


class DispatcherServiceTests(unittest.TestCase):
    def test_loop_survives_failures_and_stops(self):
        stop = threading.Event()
        calls = []

        def run_once(quiet_empty):
            calls.append(quiet_empty)
            if len(calls) == 1:
                raise RuntimeError("database not ready")
            if len(calls) == 3:
                stop.set()
        dispatcher_service.serve(run_once, stop, poll_seconds=0.01)
        self.assertEqual(calls, [True, True, True])


class PrepareServerTests(unittest.TestCase):
    def test_postgres_memory_by_server_size(self):
        text16 = prep.pg_conf_text(16, llm=False)
        self.assertIn("shared_buffers = '4GB'", text16)
        self.assertIn("effective_cache_size = '10GB'", text16)
        self.assertIn("timezone = 'Asia/Baku'", text16)
        self.assertIn("listen_addresses = 'localhost'", text16)
        self.assertIn("shared_buffers = '8GB'", prep.pg_conf_text(32, llm=False))
        with_llm = prep.pg_conf_text(31, llm=True)
        self.assertIn("shared_buffers = '2GB'", with_llm)
        self.assertIn("effective_cache_size = '6GB'", with_llm)
        self.assertIn("local AI model", with_llm)

    def test_without_apply_nothing_runs(self):
        executed = []
        steps = [("A1 time zone -> Asia/Baku", [["timedatectl", "set-timezone", "Asia/Baku"]])]
        with mock.patch.object(prep, "plan", return_value=steps), mock.patch.object(prep, "execute", executed.append), \
                mock.patch.object(prep.os, "geteuid", return_value=0), mock.patch.object(prep.shutil, "which", return_value="/x"):
            self.assertEqual(prep.main([]), 0)
            self.assertEqual(executed, [])
            self.assertEqual(prep.main(["--apply"]), 0)
            self.assertEqual(executed, [["timedatectl", "set-timezone", "Asia/Baku"]])


class ImportDbTests(Temp):
    def test_roles_script(self):
        text = ('CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER LOGIN;\nCREATE ROLE dwh_admin;\n'
                'ALTER ROLE dwh_admin WITH SUPERUSER LOGIN;\nCREATE ROLE "dwh_app";\nGRANT dwh_app TO dwh_admin;\n')
        out = imp.roles_script(text)
        self.assertNotIn("postgres", out)
        self.assertIn("rolname = 'dwh_admin'", out)
        self.assertIn("rolname = 'dwh_app'", out)
        self.assertIn("ALTER ROLE dwh_admin WITH SUPERUSER LOGIN;", out)
        self.assertIn("GRANT dwh_app TO dwh_admin;", out)

    def test_linux_create_script(self):
        script = (b"SET client_encoding = 'WIN1251';\r\n"
                  b"CREATE DATABASE dwh WITH TEMPLATE = template0 ENCODING = 'WIN1251' LOCALE_PROVIDER = libc "
                  b"LOCALE = 'Russian_Russia.1251';\r\n"
                  b"ALTER DATABASE dwh SET lc_messages TO 'Russian_Russia.1251';\r\n"
                  b"ALTER DATABASE dwh SET search_path TO 'app';\r\n"
                  b"\\connect dwh\r\nCOPY t FROM stdin;\r\n\xe0\xe1\r\n\\.\r\n")
        known = {"c", "posix", "c.utf8", "en_us.utf8"}
        out, notes = imp.linux_create_script(script, "en_US.UTF-8", known)
        self.assertIn(b"ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.UTF-8';", out)
        self.assertIn(b"-- (I16 import, Windows locale) ALTER DATABASE dwh SET lc_messages", out)
        self.assertIn(b"ALTER DATABASE dwh SET search_path TO 'app';", out)
        self.assertIn(b"\xe0\xe1", out)
        self.assertEqual(len(notes), 2)
        same, notes = imp.linux_create_script(b"CREATE DATABASE dwh WITH TEMPLATE = template0 ENCODING = 'UTF8' "
                                              b"LOCALE = 'C';\nALTER DATABASE dwh SET lc_time TO 'C.utf8';\n",
                                              "en_US.UTF-8", known)
        self.assertIn(b"LOCALE = 'C';", same)
        self.assertIn(b"\nALTER DATABASE dwh SET lc_time TO 'C.utf8';", same)
        self.assertEqual(notes, [])
        with self.assertRaises(imp.ImportError_):
            imp.linux_create_script(b"SELECT 1;\n", "en_US.UTF-8", known)

    def test_compare_and_sums(self):
        self.assertEqual(imp.compare("rows|a.t|3\nfunctions|a|2\n", "rows|a.t|3\nfunctions|a|2\n"), [])
        problems = imp.compare("rows|a.t|3\nrows|a.u|1\n", "rows|a.t|2\n")
        self.assertEqual(problems, ["rows|a.t: PC 3, server 2", "rows|a.u: PC 1, server -"])
        for name, data in {"roles.sql": b"r", "dwh.dump": b"d", "info.txt": b"owner=x", "counts.txt": b""}.items():
            (self.dir / name).write_bytes(data)
        (self.dir / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((self.dir / n).read_bytes()).hexdigest()}  {n}\n"
                                                     for n in ("roles.sql", "dwh.dump", "info.txt", "counts.txt")))
        imp.check_sums(self.dir)
        (self.dir / "dwh.dump").write_bytes(b"changed")
        with self.assertRaises(imp.ImportError_):
            imp.check_sums(self.dir)


class SettingsTests(unittest.TestCase):
    def test_env_file_override(self):
        code = ("import os,sys; sys.path[:0]=[%r,%r]; import dwh_api.config as c, dwh.config as e; "
                "print(c.ENV_PATH); print(e.get_dwh_config().host, e.get_dwh_config().port)")
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("DWH_DB_NAME=dwh\nDWH_ETL_PASSWORD=x\nDWH_APP_PASSWORD=x\nDWH_DB_HOST=db.local\nDWH_DB_PORT=5432\n")
        self.addCleanup(os.unlink, handle.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith("DWH_")}
        env["DWH_ENV_FILE"] = handle.name
        out = subprocess.run([sys.executable, "-c", code % (str(PROJECT / "backend" / "src"),
                                                            str(PROJECT / "etl" / "src"))],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split("\n")[:2], [handle.name, "db.local 5432"])


class SqlPackageTests(Temp):
    def test_marker_of_a_relation_or_a_function(self):
        self.assertEqual(sqlpkg.marker_absent({"marker": "meta.dm_mart"}), "SELECT to_regclass('meta.dm_mart') IS NULL")
        self.assertEqual(sqlpkg.marker_absent({"marker": "meta.mart_builder_guard()"}),
                         "SELECT to_regprocedure('meta.mart_builder_guard()') IS NULL")

    def test_manifest_matches_repository(self):
        packages = json.loads(sqlpkg.PACKAGES.read_text(encoding="utf-8"))
        self.assertIn("016", packages)
        for name, package in packages.items():
            for key in ("migration", "down", "check", "test", "previous_check"):
                path = PROJECT / package[key]["path"]
                self.assertEqual(sqlpkg.sha256_file(path), package[key]["sha256"].upper(), f"{name} {key}")
            self.assertIn(f"MIGRATION_{package['number']}_DONE", (PROJECT / package["migration"]["path"]).read_text())

    def test_compare_counts(self):
        before = {"rows|a.t": "5", "rows|a.add": "1", "rows|dq.rule": "14", "rows|stg.crm_retail_x": "3", "objects|a|r|3": "3"}
        package = {"added_rows": {"a.add": 2},
                   "removed": {"rows_may_decrease": ["dq.rule"], "dropped_tables": r"^stg\.crm_retail_"}}
        ok = {"rows|a.t": "5", "rows|a.add": "3", "rows|dq.rule": "0"}
        problems, notes = sqlpkg.compare_counts(before, ok, package)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["removed rows dq.rule: 14 -> 0", "dropped stg.crm_retail_x (3 rows)"])
        problems, _ = sqlpkg.compare_counts(before, dict(ok, **{"rows|dq.rule": "15"}), package)
        self.assertEqual(problems, ["dq.rule: 14 -> 15 (may only decrease)"])
        problems, _ = sqlpkg.compare_counts(before, dict(ok, **{"rows|stg.crm_retail_x": "3"}), package)
        self.assertEqual(problems, ["stg.crm_retail_x: should be dropped, still exists"])
        problems, _ = sqlpkg.compare_counts(before, dict(ok, **{"rows|a.t": "4"}), package)
        self.assertEqual(problems, ["a.t: 5 -> 4 (expected 5)"])
        problems, _ = sqlpkg.compare_counts({"rows|a.t": "5"}, {"rows|a.t": "5"}, {})
        self.assertEqual(problems, [])

    def test_check_result(self):
        run = sqlpkg.Run(Path(self.tmp.name) / "r.txt")
        ok = subprocess.CompletedProcess([], 0, "on|dwh|dwh_app|17\na|PASS\nb|PASS\n", "")
        sqlpkg.check_result(run, "c", ok, 2)
        with self.assertRaises(sqlpkg.Stop):
            sqlpkg.check_result(run, "c", subprocess.CompletedProcess([], 0, "a|PASS\nb|FAIL\n", ""), 2)
        with self.assertRaises(sqlpkg.Stop):
            sqlpkg.check_result(run, "c", ok, 3)
        with self.assertRaises(sqlpkg.Stop):
            sqlpkg.check_result(run, "c", subprocess.CompletedProcess([], 3, "a|PASS\nb|PASS\n", "boom"), 2)


class AiSetupTests(Temp):
    def args(self, **kw):
        values = dict(bin="/opt/b/llama-server", model_dir="/var/lib/m", model_file="m.gguf", mmproj="p.gguf",
                      label="gemma", threads=8, context=16384, rotate_password=False)
        values.update(kw)
        return mock.Mock(**values)

    def test_env_with_keeps_other_lines(self):
        text = "# head\nDWH_DB_HOST=127.0.0.1\nDWH_APP_PASSWORD=x\nDWH_AI_MODEL_LABEL=old\n"
        out = aisetup.env_with(text, {"DWH_AI_MODEL_LABEL": "gemma", "DWH_AI_DB_PASSWORD": "p"})
        self.assertEqual(out.splitlines()[:4], ["# head", "DWH_DB_HOST=127.0.0.1", "DWH_APP_PASSWORD=x",
                                                "DWH_AI_MODEL_LABEL=gemma"])
        self.assertIn("DWH_AI_DB_PASSWORD=p", out)
        self.assertEqual(aisetup.read_env_text(out)["DWH_AI_DB_PASSWORD"], "p")

    def test_llm_env_and_unit(self):
        text = aisetup.llm_env_text(self.args())
        self.assertIn("LLM_MODEL=/var/lib/m/m.gguf", text)
        self.assertIn("LLM_MMPROJ=/var/lib/m/p.gguf", text)
        unit = aisetup.UNIT_SRC.read_text()
        for name in ("LLM_BIN", "LLM_MODEL", "LLM_MMPROJ", "LLM_THREADS", "LLM_CONTEXT"):
            self.assertIn('"$' + name + '"', unit)
        self.assertIn("--host 127.0.0.1", unit)
        self.assertIn("IPAddressDeny=any", unit)
        self.assertNotIn("dwh-llm.service", up.UNITS)

    def test_plan(self):
        root = Path(self.tmp.name)
        env = root / "dwh.env"
        env.write_text("DWH_APP_PASSWORD=x\n")
        patches = {"LLM_ENV": root / "llm.env", "UNIT_DST": root / "dwh-llm.service", "SYSCTL": root / "90.conf",
                   "DWH_ENV": env}
        connect = mock.Mock(return_value=False)
        with mock.patch.multiple(aisetup, has_connect=connect, **patches), \
                mock.patch.object(prep, "PG_CONF", root / "dwh.conf"), mock.patch.object(prep, "memory_gb", return_value=31):
            codes = [c for c, _ in aisetup.plan(self.args())]
            self.assertEqual(codes, ["L1", "L2", "L3", "L4", "L5", "L7"])
            connect.return_value = True
            (root / "llm.env").write_text(aisetup.llm_env_text(self.args()))
            (root / "dwh-llm.service").write_text(aisetup.UNIT_SRC.read_text())
            (root / "90.conf").write_text(aisetup.SYSCTL_TEXT)
            (root / "dwh.conf").write_text(prep.pg_conf_text(31, llm=True))
            env.write_text("DWH_AI_DB_PASSWORD=p\nDWH_AI_LLM_URL=http://127.0.0.1:8081\nDWH_AI_MODEL_LABEL=gemma\n")
            self.assertEqual(aisetup.plan(self.args()), [])
            self.assertEqual([c for c, _ in aisetup.plan(self.args(rotate_password=True))], ["L5"])


if __name__ == "__main__":
    unittest.main()
