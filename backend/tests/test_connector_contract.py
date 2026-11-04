"""Connector adapter contract (I03-04) with a synthetic in-memory adapter."""

import unittest
from unittest import mock

from dwh_api import connector_contract as cc


class MemoryAdapter:
    adapter_code = "MEMORY_TEST"
    source_kind = "DATABASE"

    def __init__(self):
        self.rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"}]

    def test_connection(self, config, credential):
        if credential is None or credential.reveal() != "synthetic":
            return cc.ConnectionTestResult(False, "Authentication failed")
        return cc.ConnectionTestResult(True, "Connected")

    def discover(self, config, credential):
        return [cc.DiscoveredObject("public", "items", "TABLE", (
            cc.DiscoveredColumn("id", "bigint", False, 1),
            cc.DiscoveredColumn("name", "text", True, 2),
        ))]

    def read(self, config, credential, request):
        for row in self.rows:
            yield {column: row[column] for column in request.columns}


class ConnectorContractTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(cc._ADAPTERS, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_register_and_use(self):
        adapter = MemoryAdapter()
        cc.register_adapter(adapter)
        self.assertIs(cc.get_adapter("MEMORY_TEST"), adapter)
        self.assertEqual(cc.registered_adapters(), ("MEMORY_TEST",))
        secret = cc.SecretValue("synthetic")
        self.assertTrue(adapter.test_connection({}, secret).ok)
        self.assertFalse(adapter.test_connection({}, None).ok)
        (obj,) = adapter.discover({}, secret)
        self.assertEqual([c.name for c in obj.columns], ["id", "name"])
        rows = list(adapter.read({}, secret, cc.ReadRequest("public", "items", ("id",))))
        self.assertEqual(rows, [{"id": 1}, {"id": 2}, {"id": 3}])

    def test_rejects_invalid_adapters(self):
        class NoRead:
            adapter_code = "NO_READ"
            source_kind = "DATABASE"

            def test_connection(self, config, credential): ...

            def discover(self, config, credential): ...

        bad_code = MemoryAdapter()
        bad_code.adapter_code = "lower-case"
        bad_kind = MemoryAdapter()
        bad_kind.adapter_code = "BAD_KIND"
        bad_kind.source_kind = "FTP"
        for adapter in (NoRead(), bad_code, bad_kind):
            with self.subTest(adapter=adapter.adapter_code), self.assertRaises(cc.ConnectorError):
                cc.register_adapter(adapter)

    def test_duplicate_and_unknown(self):
        cc.register_adapter(MemoryAdapter())
        with self.assertRaises(cc.ConnectorError):
            cc.register_adapter(MemoryAdapter())
        with self.assertRaises(cc.ConnectorError):
            cc.get_adapter("ORACLE")

    def test_secret_value_is_hidden(self):
        secret = cc.SecretValue("synthetic")
        self.assertNotIn("synthetic", repr(secret))
        self.assertNotIn("synthetic", str(secret))
        self.assertNotIn("synthetic", f"{secret}")
        self.assertEqual(secret.reveal(), "synthetic")
        for bad in ("", None, 5):
            with self.subTest(bad=bad), self.assertRaises(cc.ConnectorError):
                cc.SecretValue(bad)


if __name__ == "__main__":
    unittest.main()
