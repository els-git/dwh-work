"""ETL credential READ from SecretStore (I04-05) with a fake connection."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ETL_SRC = Path(__file__).resolve().parents[2] / "etl" / "src"
if str(ETL_SRC) not in sys.path:
    sys.path.insert(0, str(ETL_SRC))
os.environ.setdefault("DWH_DB_NAME", "unit-test-not-used")
os.environ.setdefault("DWH_ETL_PASSWORD", "unit-test-not-used")

import psycopg

from dwh import config as etl_config
from dwh import source_secret as ss
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_store import SecretStore

SYNTHETIC = "synthetic-crm-Ж-9"
REF = "55555555-5555-4555-8555-555555555555"
OP = "66666666-6666-4666-8666-666666666666"


class Refused(psycopg.Error):
    __slots__ = ()

    @property
    def diag(self):
        return mock.Mock(message_primary="Source connection is disabled")


class Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.calls.append((sql, params))
        if "read_begin" in sql and self.conn.refuse:
            raise Refused("refused")

    def fetchone(self):
        return (OP, REF, "K_T")


class Conn:
    def __init__(self, refuse=False):
        self.refuse = refuse
        self.calls = []
        self.closed = False

    def cursor(self):
        return Cur(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class EtlSourceSecretTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get("DWH_I02_TEST_TEMP") or None)
        self.addCleanup(self.dir.cleanup)
        self.crypto = SecretStore(os.urandom(32))
        EncryptedFileSecretStore(crypto=self.crypto, directory=Path(self.dir.name)).create(secret_ref=REF, value=SYNTHETIC)
        patcher = mock.patch.dict(os.environ, {ss.SECRET_DIRECTORY_ENV: self.dir.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, conn, crypto=None):
        return ss.read_source_credential("CRM_RETAIL", open_connection=lambda: conn,
                                         key_loader=lambda k: crypto or self.crypto,
                                         store_factory=EncryptedFileSecretStore)

    def test_reads_and_journals(self):
        conn = Conn()
        self.assertEqual(self.read(conn), SYNTHETIC)
        self.assertEqual(conn.calls[0][1], ("CRM_RETAIL",))
        self.assertEqual(conn.calls[1][1], (OP, True))
        self.assertTrue(conn.closed)
        for sql, params in conn.calls:
            self.assertNotIn(SYNTHETIC, repr(params))

    def test_wrong_key_finishes_failed(self):
        conn = Conn()
        with self.assertRaises(ss.SourceSecretError) as ctx:
            self.read(conn, crypto=SecretStore(os.urandom(32)))
        self.assertEqual(conn.calls[1][1], (OP, False))
        self.assertNotIn(SYNTHETIC, str(ctx.exception))

    def test_refused_by_database(self):
        conn = Conn(refuse=True)
        with self.assertRaises(ss.SourceSecretError) as ctx:
            self.read(conn)
        self.assertIn("disabled", str(ctx.exception))
        self.assertEqual(len(conn.calls), 1)
        self.assertTrue(conn.closed)

    def test_not_configured(self):
        with mock.patch.dict(os.environ, {ss.SECRET_DIRECTORY_ENV: ""}):
            with self.assertRaises(ss.SourceSecretError):
                self.read(Conn())


class CrmConfigTests(unittest.TestCase):
    ENV = {"CRM_DB_HOST": "crm.local", "CRM_DB_NAME": "vtb", "CRM_DB_USER": "vtb"}

    def test_secretstore_when_env_password_absent(self):
        with mock.patch.dict(os.environ, self.ENV), mock.patch.dict(os.environ, {"CRM_DB_PASSWORD": ""}), \
                mock.patch.object(ss, "read_source_credential", return_value=SYNTHETIC) as reader:
            cfg = etl_config.get_crm_config()
        reader.assert_called_once_with("CRM_RETAIL")
        self.assertEqual(cfg.password, SYNTHETIC)

    def test_legacy_env_password_still_used_with_warning(self):
        with mock.patch.dict(os.environ, {**self.ENV, "CRM_DB_PASSWORD": "legacy-pw"}), \
                mock.patch.object(ss, "read_source_credential") as reader, \
                mock.patch("sys.stderr") as stderr:
            cfg = etl_config.get_crm_config()
        reader.assert_not_called()
        self.assertEqual(cfg.password, "legacy-pw")
        self.assertIn("WARNING", "".join(str(c.args[0]) for c in stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
