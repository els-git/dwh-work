"""I03-05 (package 028): routes and error mapping of the schema/name change and deletion of a source object."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("DWH_APP_PASSWORD", "unused")

from fastapi import HTTPException  # noqa: E402

from dwh_api import integration_admin as ia  # noqa: E402


class _Diag:
    def __init__(self, message):
        self.message_primary = message


class _DbError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.diag = _Diag(message)


def status_of(message: str) -> int:
    try:
        ia._raise_database_error(_DbError(message))
    except HTTPException as exc:
        return exc.status_code
    raise AssertionError("no HTTP error raised")


class IdentityRoutesTest(unittest.TestCase):
    def test_routes_are_registered(self):
        routes = {(sorted(r.methods)[0], r.path) for r in ia.router.routes if hasattr(r, "methods")}
        base = "/api/admin/integrations/source-objects/{source_object_id}"
        self.assertIn(("GET", base + "/usage"), routes)
        self.assertIn(("PUT", base + "/identity"), routes)
        self.assertIn(("DELETE", base), routes)

    def test_refusals_of_the_database_are_client_errors(self):
        self.assertEqual(status_of("Source object is in use and keeps its schema and name: etl.object_run"), 422)
        self.assertEqual(status_of("Source object is in use and cannot be deleted: meta.source_object_snapshot"), 422)
        self.assertEqual(status_of("Source object already exists in this source system: public.app_users"), 422)
        self.assertEqual(status_of("Source schema is required"), 422)
        self.assertEqual(status_of("Source object does not exist"), 404)
        self.assertEqual(status_of("Integration administration access denied"), 403)
        self.assertEqual(status_of("Authentication required"), 401)

    def test_identity_payload_limits(self):
        with self.assertRaises(Exception):
            ia.SourceObjectIdentityRequest(source_schema="", source_object="x")
        with self.assertRaises(Exception):
            ia.SourceObjectIdentityRequest(source_schema="public", source_object="x" * 129)
        ok = ia.SourceObjectIdentityRequest(source_schema="public", source_object="services")
        self.assertEqual((ok.source_schema, ok.source_object), ("public", "services"))


if __name__ == "__main__":
    unittest.main()
