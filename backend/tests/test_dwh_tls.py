"""I13-10: tools/server/dwh_tls.py with a test CA and real openssl; nginx and systemctl are replaced."""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
import dwh_tls  # noqa: E402

PASSWORD = "Pfx-Test-Pass-1"
NAME = "dwh.vtbaze.local"
IP = "10.1.20.20"


def ossl(*args, stdin=None):
    subprocess.run(["openssl", *args], input=stdin, check=True, capture_output=True, text=True)


class Ca:
    """Root CA, optional intermediate, server certificate and PFX files in a temporary folder."""

    def __init__(self, d: Path):
        self.d = d
        ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "root.key"), "-out",
             str(d / "root.crt"), "-days", "30", "-subj", "/DC=local/DC=VTBAZE/CN=ROOT-CA",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")

    def leaf(self, tag: str, san: str, days: int = 400, via_intermediate: bool = False) -> tuple[Path, Path]:
        d = self.d
        issuer_crt, issuer_key = d / "root.crt", d / "root.key"
        if via_intermediate:
            ossl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "inter.key"), "-out", str(d / "inter.csr"),
                 "-subj", "/CN=SUB-CA")
            (d / "inter.ext").write_text("basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n")
            ossl("x509", "-req", "-in", str(d / "inter.csr"), "-CA", str(d / "root.crt"), "-CAkey", str(d / "root.key"),
                 "-CAcreateserial", "-out", str(d / "inter.crt"), "-days", "30", "-extfile", str(d / "inter.ext"))
            issuer_crt, issuer_key = d / "inter.crt", d / "inter.key"
        key, csr, crt, ext = (d / f"{tag}.{x}" for x in ("key", "csr", "crt", "ext"))
        ossl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(csr), "-subj", "/CN=dwh")
        ext.write_text(f"subjectAltName={san}\nextendedKeyUsage=serverAuth\n")
        ossl("x509", "-req", "-in", str(csr), "-CA", str(issuer_crt), "-CAkey", str(issuer_key), "-CAcreateserial",
             "-out", str(crt), "-days", str(days), "-extfile", str(ext))
        return key, crt

    def pfx(self, tag: str, key: Path, crt: Path, chain: list[Path], legacy: bool = False) -> Path:
        bundle = self.d / f"{tag}.chain.pem"
        bundle.write_text("".join(p.read_text() for p in chain))
        out = self.d / f"{tag}.pfx"
        args = ["pkcs12", "-export", "-inkey", str(key), "-in", str(crt), "-passout", "stdin", "-out", str(out)]
        if chain:
            args += ["-certfile", str(bundle)]
        if legacy:
            args.insert(2, "-legacy")
        ossl(*args, stdin=PASSWORD + "\n")
        return out


class FakeSystem:
    def __init__(self, nginx_ok=True):
        self.calls, self.nginx_ok = [], nginx_ok

    def __call__(self, cmd, stdin=None, check=True):
        if cmd[0] in ("nginx", "systemctl"):
            self.calls.append(" ".join(cmd))
            if cmd[0] == "nginx" and not self.nginx_ok:
                raise dwh_tls.TlsError("nginx -t: emerg duplicate default server")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return dwh_tls.run(cmd, stdin, check)


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class TlsToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.ca = Ca(Path(cls._tmp.name))
        san = f"DNS:dwh,DNS:{NAME},IP:{IP}"
        key, crt = cls.ca.leaf("good", san)
        cls.good = cls.ca.pfx("good", key, crt, [cls.ca.d / "root.crt"])
        cls.good_crt = crt.read_text()
        cls.legacy = cls.ca.pfx("legacy", key, crt, [cls.ca.d / "root.crt"], legacy=True)
        cls.no_root = cls.ca.pfx("noroot", key, crt, [])
        key2, crt2 = cls.ca.leaf("other", "DNS:other.vtbaze.local")
        cls.other = cls.ca.pfx("other", key2, crt2, [cls.ca.d / "root.crt"])
        key3, crt3 = cls.ca.leaf("short", san, days=10)
        cls.short = cls.ca.pfx("short", key3, crt3, [cls.ca.d / "root.crt"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        d = Path(self._dir.name)
        self.tls = d / "tls"
        self.tls.mkdir()
        (self.tls / "dwh.crt").write_text("OLD CERT\n")
        (self.tls / "dwh.key").write_text("OLD KEY\n")
        self.conf = d / "dwh.conf"
        self.conf.write_text("server { server_name _; }\n")
        self.live_calls = []

    def live(self, ok=True):
        def fake(name, ip, ca, want, port=443):
            self.live_calls.append((name, ip, Path(ca).read_text().count("BEGIN CERTIFICATE"), want))
            return [(ok, f"https://{name}")]
        return fake

    def main(self, pfx, *extra, password=PASSWORD, system=None, live_ok=True, action="install"):
        argv = [action, "--tls-dir", str(self.tls), "--nginx-conf", str(self.conf)]
        if action == "install":
            argv += ["--pfx", str(pfx), "--name", NAME, "--ip", IP]
        out = io.StringIO()
        with redirect_stdout(out):
            code = dwh_tls.main(argv + list(extra), password_prompt=lambda _: password,
                                runner=system or FakeSystem(), live=self.live(live_ok))
        return code, out.getvalue()

    def assert_unchanged(self):
        self.assertEqual((self.tls / "dwh.crt").read_text(), "OLD CERT\n")
        self.assertEqual((self.tls / "dwh.key").read_text(), "OLD KEY\n")
        self.assertEqual(self.conf.read_text(), "server { server_name _; }\n")
        self.assertFalse((self.tls / "ca.crt").exists())

    def test_plan_changes_nothing(self):
        code, out = self.main(self.good)
        self.assertEqual(code, 0, out)
        self.assertIn("TLS_RESULT: PLAN", out)
        self.assertIn(f"server_name {NAME};", out)
        self.assertIn(f"return 301 https://{NAME}$request_uri;", out)
        self.assertNotIn(PASSWORD, out)
        self.assert_unchanged()
        self.assertEqual(sorted(p.name for p in self.tls.iterdir()), ["dwh.crt", "dwh.key"])   # no key copy left

    def test_refusals_change_nothing(self):
        for pfx, password, reason in ((self.good, "wrong", "PFX not read"),
                                      (self.other, PASSWORD, f"FAIL  certificate is for {NAME}"),
                                      (self.short, PASSWORD, "FAIL  certificate valid more than 30 days"),
                                      (self.no_root, PASSWORD, "root CA: not in the PFX")):
            code, out = self.main(pfx, "--apply", password=password)
            self.assertEqual(code, 1, out)
            self.assertIn(reason, out)
            self.assertIn("TLS_RESULT: FAIL", out)
            self.assertNotIn(PASSWORD, out)
            self.assert_unchanged()

    def test_root_from_ca_file(self):
        code, out = self.main(self.no_root, "--ca-file", str(self.ca.d / "root.crt"))
        self.assertEqual(code, 0, out)

    def test_legacy_pfx(self):
        code, out = self.main(self.legacy)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  chain verifies", out)

    def test_apply_then_rollback(self):
        system = FakeSystem()
        code, out = self.main(self.good, "--apply", system=system)
        self.assertEqual(code, 0, out)
        self.assertIn(f"TLS_RESULT: PASS (https://{NAME})", out)
        self.assertEqual(system.calls, ["nginx -t", "systemctl reload nginx"])
        crt = (self.tls / "dwh.crt").read_text()
        self.assertEqual(crt.count("BEGIN CERTIFICATE"), 1)                # root CA is not served
        self.assertEqual(dwh_tls.fingerprint(crt), dwh_tls.fingerprint(self.good_crt))
        self.assertIn("PRIVATE KEY", (self.tls / "dwh.key").read_text())
        self.assertEqual((self.tls / "dwh.key").stat().st_mode & 0o777, 0o600)
        self.assertIn("ROOT-CA", subprocess.run(["openssl", "x509", "-in", str(self.tls / "ca.crt"), "-noout",
                                                 "-subject"], capture_output=True, text=True).stdout)
        conf = self.conf.read_text()
        self.assertNotIn("dwh.example.local", conf)
        self.assertEqual(conf.count(f"server_name {NAME};"), 1)
        self.assertEqual(self.live_calls[0][:3], (NAME, IP, 1))
        copies = list((self.tls / "backup").iterdir())
        self.assertEqual(len(copies), 1)
        self.assertEqual((copies[0] / "dwh.key").read_text(), "OLD KEY\n")
        code, out = self.main(None, action="rollback", system=system)
        self.assertEqual(code, 0, out)
        self.assert_unchanged()
        self.assertEqual(list((self.tls / "backup").iterdir()), [])

    def test_failed_live_check_rolls_back(self):
        code, out = self.main(self.good, "--apply", "--wait", "0", live_ok=False)
        self.assertEqual(code, 1, out)
        self.assertIn("TLS_RESULT: FAIL (rolled back)", out)
        self.assert_unchanged()

    def test_live_check_waits_for_new_workers(self):
        answers = iter([False, False, True])         # old nginx workers answer first

        def fake(name, ip, ca, want, port=443):
            self.live_calls.append(name)
            return [(next(answers), "served certificate")]
        out = io.StringIO()
        with redirect_stdout(out), unittest.mock.patch.object(dwh_tls.time, "sleep"):
            code = dwh_tls.main(["install", "--tls-dir", str(self.tls), "--nginx-conf", str(self.conf), "--pfx",
                                 str(self.good), "--name", NAME, "--ip", IP, "--apply"],
                                password_prompt=lambda _: PASSWORD, runner=FakeSystem(), live=fake)
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(len(self.live_calls), 3)

    def test_failed_nginx_test_rolls_back(self):
        code, out = self.main(self.good, "--apply", system=FakeSystem(nginx_ok=False))
        self.assertEqual(code, 1, out)
        self.assertIn("duplicate default server", out)
        self.assert_unchanged()

    def test_intermediate_is_served(self):
        key, crt = self.ca.leaf("viainter", f"DNS:{NAME},IP:{IP}", via_intermediate=True)
        pfx = self.ca.pfx("viainter", key, crt, [self.ca.d / "inter.crt", self.ca.d / "root.crt"])
        code, out = self.main(pfx, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("1 intermediate", out)
        self.assertEqual((self.tls / "dwh.crt").read_text().count("BEGIN CERTIFICATE"), 2)

    def test_arguments(self):
        for argv in (["install", "--pfx", str(self.good), "--name", "dwh"],
                     ["install", "--pfx", "/no/such.pfx", "--name", NAME],
                     ["install", "--pfx", str(self.good), "--name", NAME, "--ip", "x.y"]):
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                    unittest.mock.patch("sys.stderr", io.StringIO()):
                dwh_tls.parse(argv)

    def test_template_has_both_servers(self):
        text = dwh_tls.render_nginx(dwh_tls.TEMPLATE, NAME)
        self.assertEqual(text.count("default_server"), 2)
        self.assertEqual(text.count("ssl_certificate "), 2)
        self.assertIn(f"return 301 https://{NAME}$request_uri;", text)


if __name__ == "__main__":
    unittest.main()
