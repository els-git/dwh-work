"""I17 step 5: the model client talks only to the local model service and fails closed."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from dwh_api import ai_llm


class Handler(BaseHTTPRequestHandler):
    received: list = []
    disconnected = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.received.append(body)
        if body["messages"][1]["content"] == "slow":
            # A long answer streamed slowly: the test stops it.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(100):
                    chunk = {"choices": [{"delta": {"content": "x"}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                Handler.disconnected.set()
            return
        if body.get("stream") and body["messages"][1]["content"] == "stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (" ans", "wer ", None):
                chunk = {"choices": [{"delta": {"content": piece}}]} if piece else {"choices": []}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        data = json.dumps({"choices": [{"message": {"content": " answer "}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


class LlmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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

    def test_streamed_answer(self):
        self.assertEqual(ai_llm.chat(self.settings(), "s", "stream"), "answer")
        self.assertTrue(Handler.received[-1]["stream"])

    def test_stop_frees_the_model_at_once(self):
        settings = self.settings()
        token = ai_llm.CancelToken()
        Handler.disconnected.clear()
        threading.Timer(0.5, token.cancel).start()
        started = time.monotonic()
        with self.assertRaises(ai_llm.LlmCancelled):
            ai_llm.chat(settings, "s", "slow", cancel=token)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(Handler.disconnected.wait(5), "the model service must see the connection closed")
        self.assertEqual(ai_llm.chat(settings, "s", "q"), "answer")  # the model is free again

    def test_stop_while_waiting_in_the_queue(self):
        settings = ai_llm.LlmSettings(self.url, "m", 5, 30)
        token = ai_llm.CancelToken()
        ai_llm._LOCK.acquire()
        try:
            threading.Timer(0.3, token.cancel).start()
            started = time.monotonic()
            with self.assertRaises(ai_llm.LlmCancelled):
                ai_llm.chat(settings, "s", "q", cancel=token)
            self.assertLess(time.monotonic() - started, 3)
        finally:
            ai_llm._LOCK.release()

if __name__ == "__main__":
    unittest.main()
