"""Source connection API (I03): credential flow and safety, without a database.

The database functions are replaced by a fake connection that records calls;
the encrypted file store is the real one in a temporary directory with a
random synthetic master key. No real credentials are used.
"""

import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

import psycopg
from fastapi import HTTPException

from dwh_api import source_connection_admin as sca
from dwh_api.secret_file_store import EncryptedFileSecretStore, SecretFileStoreError
from dwh_api.secret_store import SecretStore

SYNTHETIC = "synthetic-credential-Ж-123"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
SECRET_REF = "22222222-2222-4222-8222-222222222222"


class FakeDbError(psycopg.Error):
    def __init__(self, message):
        super().__init__(message)
        self._message = message

    @property
    def diag(self):
        return mock.Mock(message_primary=self._message)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.calls.append((sql, params))
        if "set_config" in sql:
            return
        handler = self.conn.handlers.get(next(k for k in self.conn.handlers if k in sql))
        result = handler(params)
        if isinstance(result, Exception):
            raise result
        self.row = result

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def begin_ok(params):
    return {"operation_id": OPERATION_ID, "secret_ref": SECRET_REF}


def complete_ok(params):
    return {"state": "ACTIVE" if params[3] else "FAILED"}


class SourceCredentialFlowTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("DWH_I02_TEST_TEMP") or None
        self.context = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.context.cleanup)
        self.directory = Path(self.context.name)
        self.store = EncryptedFileSecretStore(crypto=SecretStore(os.urandom(32)), directory=self.directory)

    def run_flow(self, handlers, store=None):
        conn = FakeConnection(handlers)
        try:
            result = sca.store_source_credential(
                lambda cfg: conn, "a" * 64, "req-1", 7, SYNTHETIC,
                store or self.store, self.directory, "K2026_01")
        except HTTPException as exc:
            return conn, exc
        return conn, result

    def complete_calls(self, conn):
        return [p for s, p in conn.calls if "secret_complete" in s]

    def test_success_begin_file_complete(self):
        conn, result = self.run_flow({"secret_begin": begin_ok, "secret_complete": complete_ok})
        self.assertEqual(result.credential_state, "ACTIVE")
        self.assertEqual(result.credential_key_id, "K2026_01")
        self.assertEqual(conn.commits, 2, "ATTEMPT+PENDING and OUTCOME are committed separately")
        envelope = (self.directory / f"{SECRET_REF}.bin").read_bytes()
        (params,) = self.complete_calls(conn)
        self.assertEqual(params, ("a" * 64, 7, OPERATION_ID, True, hashlib.sha256(envelope).hexdigest(), "K2026_01"))
        self.assertRegex(params[4], r"^[0-9a-f]{64}$")

    def test_value_never_reaches_database_or_file_in_plaintext(self):
        conn, _ = self.run_flow({"secret_begin": begin_ok, "secret_complete": complete_ok})
        for sql, params in conn.calls:
            self.assertNotIn(SYNTHETIC, sql)
            self.assertNotIn(SYNTHETIC, repr(params))
        envelope = (self.directory / f"{SECRET_REF}.bin").read_bytes()
        self.assertNotIn(SYNTHETIC.encode("utf-8"), envelope)
        self.assertEqual(self.store.read(secret_ref=SECRET_REF), SYNTHETIC)

    def test_file_failure_records_failed_outcome(self):
        failing = mock.Mock(spec=EncryptedFileSecretStore)
        failing.create.side_effect = SecretFileStoreError("Unable to save encrypted secret")
        conn, exc = self.run_flow({"secret_begin": begin_ok, "secret_complete": complete_ok}, store=failing)
        self.assertIsInstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 500)
        (params,) = self.complete_calls(conn)
        self.assertEqual(params[3], False)
        self.assertIsNone(params[4])
        self.assertNotIn(SYNTHETIC, exc.detail)

    def test_complete_failure_after_file_requires_reconciliation(self):
        conn, exc = self.run_flow({
            "secret_begin": begin_ok,
            "secret_complete": lambda p: FakeDbError("connection lost"),
        })
        self.assertEqual(exc.status_code, 500)
        self.assertIn("reconciliation required", exc.detail)
        self.assertEqual(len(self.complete_calls(conn)), 1, "no automatic retry")
        self.assertTrue((self.directory / f"{SECRET_REF}.bin").exists())

    def test_begin_conflict_is_409_and_no_file(self):
        conn, exc = self.run_flow({
            "secret_begin": lambda p: FakeDbError("Another credential operation for this source is in progress"),
            "secret_complete": complete_ok,
        })
        self.assertEqual(exc.status_code, 409)
        self.assertEqual(self.complete_calls(conn), [])
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)


class CredentialInputTests(unittest.TestCase):
    def test_rejects_without_echoing_value(self):
        long_value = "x" * (sca.CREDENTIAL_MAX_LENGTH + 1)
        for payload in ({}, {"value": ""}, {"value": 123}, {"value": long_value}, [SYNTHETIC], SYNTHETIC):
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(HTTPException) as ctx:
                    sca._credential_value(payload)
                self.assertEqual(ctx.exception.status_code, 422)
                self.assertNotIn(SYNTHETIC, ctx.exception.detail)
                self.assertNotIn(long_value, ctx.exception.detail)

    def test_accepts_value(self):
        self.assertEqual(sca._credential_value({"value": SYNTHETIC}), SYNTHETIC)


class CredentialStorageConfigTests(unittest.TestCase):
    def setUp(self):
        self.context = tempfile.TemporaryDirectory(dir=os.environ.get("DWH_I02_TEST_TEMP") or None)
        self.addCleanup(self.context.cleanup)
        self.key = base64.b64encode(os.urandom(32)).decode()

    def env(self, **values):
        base = {
            "DWH_CONNECTOR_MASTER_KEY_B64": self.key,
            sca.SECRET_DIRECTORY_ENV: self.context.name,
            sca.MASTER_KEY_ID_ENV: "K2026_01",
        }
        base.update(values)
        return mock.patch.dict(os.environ, {k: v for k, v in base.items() if v is not None}, clear=True)

    def test_configured(self):
        with self.env():
            store, directory, key_id = sca.credential_storage()
        self.assertEqual((directory, key_id), (Path(self.context.name), "K2026_01"))
        self.assertIsInstance(store, EncryptedFileSecretStore)

    def test_fails_closed(self):
        cases = {
            "no key": {"DWH_CONNECTOR_MASTER_KEY_B64": None},
            "bad key": {"DWH_CONNECTOR_MASTER_KEY_B64": "not-base64"},
            "no directory": {sca.SECRET_DIRECTORY_ENV: None},
            "missing directory": {sca.SECRET_DIRECTORY_ENV: os.path.join(self.context.name, "absent")},
            "no key id": {sca.MASTER_KEY_ID_ENV: None},
            "bad key id": {sca.MASTER_KEY_ID_ENV: "key with spaces"},
        }
        for name, change in cases.items():
            with self.subTest(name), self.env(**change):
                with self.assertRaises(sca.CredentialStorageUnavailable):
                    sca.credential_storage()


class ErrorMappingTests(unittest.TestCase):
    def test_messages(self):
        cases = {
            "Connection not found, of another kind or changed by another user": 409,
            "Another credential operation for this source is in progress": 409,
            "Connection settings must not contain secrets": 422,
            "API sources keep connection settings and credentials on their objects": 422,
            "READY requires a successful connection test (available from I04)": 422,
            "Save the connection settings of the source first": 422,
            "Source connection is disabled": 422,
            "Source has no active credential": 422,
            "Authentication required": 401,
            "Integration administration access denied": 403,
            "Source system not found": 404,
        }
        for message, status in cases.items():
            with self.subTest(message):
                with self.assertRaises(HTTPException) as ctx:
                    sca._raise_connection_error(FakeDbError(message))
                self.assertEqual(ctx.exception.status_code, status)


class RouteTests(unittest.TestCase):
    def test_routes(self):
        routes = {(r.path, tuple(sorted(r.methods))) for r in sca.router.routes}
        base = "/api/admin/integrations/source-systems/{source_system_id}/connection"
        self.assertEqual(routes, {
            (base, ("GET",)),
            (base, ("PUT",)),
            (base + "/status", ("POST",)),
            (base + "/credential", ("PUT",)),
            (base + "/credential", ("DELETE",)),
        })

    def test_no_route_returns_a_secret(self):
        for model in (sca.SourceConnection, sca.CredentialResponse, sca.RowVersionResponse):
            for name in model.model_fields:
                self.assertNotRegex(name, r"secret_ref|ciphertext|password|value")


if __name__ == "__main__":
    unittest.main()
