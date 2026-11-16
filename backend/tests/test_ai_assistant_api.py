"""I17 step 5: /api/ai is closed without AI_ASSISTANT_USE; the role comes from the server session."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

from fastapi.testclient import TestClient  # noqa: E402

from dwh_api import ai_assistant, auth  # noqa: E402
from dwh_api.main import app  # noqa: E402


def state(*codes, level="USER"):
    return SimpleNamespace(user=SimpleNamespace(account_level=level, must_change_password=False),
                           permissions=[SimpleNamespace(permission_code=c) for c in codes], resources=[])


class ApiTests(unittest.TestCase):
    def call(self, current, method="get", path="/api/ai/status", **kwargs):
        with mock.patch.object(auth, "_load_auth_state", return_value=current), \
                mock.patch.object(ai_assistant, "_load_auth_state", return_value=current), \
                mock.patch.dict(os.environ, {"DWH_AI_LLM_URL": "http://127.0.0.1:9"}):
            return getattr(TestClient(app), method)(path, **kwargs)

    def test_without_permission_forbidden(self):
        self.assertEqual(self.call(state("REPORT_VIEW")).status_code, 403)
        self.assertEqual(self.call(state("REPORT_VIEW"), "post", "/api/ai/help", json={"question": "x"}).status_code, 403)
        self.assertEqual(self.call(None).status_code, 401)

    def test_status_reflects_session_permissions(self):
        body = self.call(state("AI_ASSISTANT_USE")).json()
        self.assertEqual((body["can_help"], body["can_report"], body["can_admin"], body["model_ready"]),
                         (True, False, False, False))

    def test_image_check(self):
        good = "data:image/png;base64,iVBORw0KGgoAAAA="
        self.assertTrue(ai_assistant.check_image(good).startswith("data:image/png;base64,"))
        for bad in ("data:image/gif;base64,R0lG", "data:image/jpeg;base64,iVBORw0KGgo=", "http://x/y.png"):
            with self.assertRaises(Exception):
                ai_assistant.check_image(bad)


if __name__ == "__main__":
    unittest.main()
