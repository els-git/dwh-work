"""I17 step 5: the model client talks only to the local model service and fails closed."""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from dwh_api import ai_llm


class Handler(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.received.append(body)
        data = json.dumps({"choices": [{"message": {"content": " answer "}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


class LlmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def settings(self):
        with mock.patch.dict(os.environ, {"DWH_AI_LLM_URL": self.url}):
            return ai_llm.LlmSettings.from_env()

    def test_only_local_url(self):
        for url in ("http://10.1.20.21:8081", "https://127.0.0.1:8081", "http://example.com"):
            with mock.patch.dict(os.environ, {"DWH_AI_LLM_URL": url}):
                with self.assertRaises(ai_llm.LlmUnavailable):
                    ai_llm.LlmSettings.from_env()

    def test_chat_with_and_without_image(self):
        settings = self.settings()
        self.assertTrue(ai_llm.is_ready(settings))
        self.assertEqual(ai_llm.chat(settings, "s", "q"), "answer")
        ai_llm.chat(settings, "s", "q", "data:image/png;base64,AAAA")
        content = Handler.received[-1]["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,AAAA")

    def test_unavailable_and_busy(self):
        with mock.patch.dict(os.environ, {"DWH_AI_LLM_URL": "http://127.0.0.1:9", "DWH_AI_TIMEOUT_SECONDS": "2"}):
            settings = ai_llm.LlmSettings.from_env()
        self.assertFalse(ai_llm.is_ready(settings))
        with self.assertRaises(ai_llm.LlmUnavailable):
            ai_llm.chat(settings, "s", "q")
        busy = ai_llm.LlmSettings(self.url, "m", 5, 0)
        ai_llm._LOCK.acquire()
        try:
            with self.assertRaises(ai_llm.LlmBusy):
                ai_llm.chat(busy, "s", "q")
        finally:
            ai_llm._LOCK.release()


if __name__ == "__main__":
    unittest.main()
