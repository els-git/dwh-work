"""Client of the local model (I17 step 5): llama-server on the DWH server, OpenAI-compatible API, localhost only.

Settings (/etc/dwh/dwh.env on the server):
  DWH_AI_LLM_URL          default http://127.0.0.1:8081; any other host than 127.0.0.1/localhost is refused
  DWH_AI_MODEL_LABEL      name of the model for the journal (default gemma4-26b-a4b)
  DWH_AI_TIMEOUT_SECONDS  one answer, default 300
The model on a CPU answers one question at a time: a second question waits up to DWH_AI_QUEUE_SECONDS (default 120)
and is then refused as busy. An image (I17-07) is passed in memory as a data URL and never stored.
"""
from __future__ import annotations

import json
import os
import threading
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


def chat(settings: LlmSettings, system: str, user: str, image_data_url: str | None = None,
         max_tokens: int = 700) -> str:
    content: object = user
    if image_data_url:
        content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": image_data_url}}]
    body = json.dumps({"messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                       "temperature": 0.1, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request(settings.url + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    if not _LOCK.acquire(timeout=settings.queue_seconds):
        raise LlmBusy("the assistant is answering another question; try again later")
    try:
        with _open(request, settings.timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LlmUnavailable("the model service does not answer") from exc
    finally:
        _LOCK.release()
    try:
        return (data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmUnavailable("unexpected answer of the model service") from exc
