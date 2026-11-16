"""Client of the local model (I17 step 5): llama-server on the DWH server, OpenAI-compatible API, localhost only.

Settings (/etc/dwh/dwh.env on the server):
  DWH_AI_LLM_URL          default http://127.0.0.1:8081; any other host than 127.0.0.1/localhost is refused
  DWH_AI_MODEL_LABEL      name of the model for the journal (default gemma4-26b-a4b)
  DWH_AI_TIMEOUT_SECONDS  one answer, default 300
The model on a CPU answers one question at a time: a second question waits up to DWH_AI_QUEUE_SECONDS (default 120)
and is then refused as busy. An image (I17-07) is passed in memory as a data URL and never stored.
The answer is read as a stream: a question stopped by the user (CancelToken.cancel, the "Stop" button) closes the
connection, llama-server stops generating, and the model is free for the next question at once.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

_LOCK = threading.Lock()
ALLOWED_HOSTS = ("127.0.0.1", "localhost")


class LlmUnavailable(RuntimeError):
    pass


class LlmBusy(RuntimeError):
    pass


class LlmCancelled(RuntimeError):
    pass


class CancelToken:
    """Stops one question: while it waits for the model or while the model answers."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def attach(self, sock: socket.socket | None) -> None:
        # The socket itself is kept: http.client forgets it once the answer has started.
        with self._lock:
            self._socket = sock
            if sock is not None and self._event.is_set():
                _shutdown(sock)

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            if self._socket is not None:
                _shutdown(self._socket)

    def check(self) -> None:
        if self._event.is_set():
            raise LlmCancelled("stopped by the user")


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


@dataclass(frozen=True)
class LlmSettings:
    url: str
    model_label: str
    timeout: int
    queue_seconds: int

    @classmethod
    def from_env(cls) -> "LlmSettings":
        url = os.environ.get("DWH_AI_LLM_URL", "http://127.0.0.1:8081").rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in ALLOWED_HOSTS:
            raise LlmUnavailable("DWH_AI_LLM_URL must point to this server (http://127.0.0.1)")
        return cls(url, os.environ.get("DWH_AI_MODEL_LABEL", "gemma4-26b-a4b"),
                   int(os.environ.get("DWH_AI_TIMEOUT_SECONDS", "300")),
                   int(os.environ.get("DWH_AI_QUEUE_SECONDS", "120")))


def _open(request: urllib.request.Request, timeout: int):
    # No proxy: the model is on this server, a proxy from the environment must not be used.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def is_ready(settings: LlmSettings) -> bool:
    try:
        with _open(urllib.request.Request(settings.url + "/health"), 5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _read_answer(response: http.client.HTTPResponse) -> str:
    if "text/event-stream" not in (response.getheader("Content-Type") or ""):
        data = json.loads(response.read())
        return (data["choices"][0]["message"].get("content") or "").strip()
    parts: list[str] = []
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        for choice in json.loads(payload).get("choices") or []:
            parts.append((choice.get("delta") or {}).get("content") or "")
    return "".join(parts).strip()


def chat(settings: LlmSettings, system: str, user: str, image_data_url: str | None = None,
         max_tokens: int = 700, cancel: CancelToken | None = None) -> str:
    content: object = user
    if image_data_url:
        content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": image_data_url}}]
    body = json.dumps({"messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                       "temperature": 0.1, "max_tokens": max_tokens, "stream": True}).encode()
    parsed = urlparse(settings.url)
    token = cancel or CancelToken()
    deadline = time.monotonic() + settings.queue_seconds
    while not _LOCK.acquire(timeout=min(0.5, max(0.0, deadline - time.monotonic()))):
        token.check()
        if time.monotonic() >= deadline:
            raise LlmBusy("the assistant is answering another question; try again later")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=settings.timeout)
    try:
        token.check()
        connection.connect()
        token.attach(connection.sock)
        connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise LlmUnavailable(f"the model service answered {response.status}")
        answer = _read_answer(response)
        token.check()
        return answer
    except LlmCancelled:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        if token.cancelled:
            raise LlmCancelled("stopped by the user") from exc
        raise LlmUnavailable("the model service does not answer") from exc
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmUnavailable("unexpected answer of the model service") from exc
    finally:
        token.attach(None)
        connection.close()
        _LOCK.release()
