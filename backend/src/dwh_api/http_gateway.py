"""HTTP gateway (I11, decision B10): the only way DWH sends a request to an API source.

Rules: only GET; https (http only for hosts in DWH_HTTP_PLAIN_HOSTS); the host must be in
DWH_HTTP_ALLOWED_HOSTS (exact names, comma separated, a name may carry :port); redirects are not followed
(a 3xx answer is returned as it is and treated as an error by the caller); no proxy from the environment;
the answer is read up to DWH_HTTP_MAX_BYTES (default 50 MB). Certificates are checked with the system store
plus DWH_HTTP_CA_FILE (default /etc/dwh/tls/ca.crt when present: the root of the internal CA).

Errors carry a short code and a message without the query string, headers or credentials.
"""

from __future__ import annotations

import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Sequence

DEFAULT_CA_FILE = "/etc/dwh/tls/ca.crt"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


class HttpGatewayError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str


@dataclass
class GatewaySettings:
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)
    plain_hosts: frozenset[str] = field(default_factory=frozenset)
    ca_file: str | None = None
    max_bytes: int = DEFAULT_MAX_BYTES

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewaySettings":
        env = os.environ if env is None else env

        def names(key: str) -> frozenset[str]:
            return frozenset(h.strip().lower() for h in env.get(key, "").split(",") if h.strip())

        ca = env.get("DWH_HTTP_CA_FILE", "").strip() or (DEFAULT_CA_FILE if os.path.isfile(DEFAULT_CA_FILE) else "")
        max_bytes = env.get("DWH_HTTP_MAX_BYTES", "").strip()
        return cls(allowed_hosts=names("DWH_HTTP_ALLOWED_HOSTS"), plain_hosts=names("DWH_HTTP_PLAIN_HOSTS"),
                   ca_file=ca or None, max_bytes=int(max_bytes) if max_bytes.isdigit() else DEFAULT_MAX_BYTES)


def shown_url(url: str) -> str:
    """URL for messages: scheme, host and path only (no query: it may hold a key)."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))


def check_url(url: str, settings: GatewaySettings) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise HttpGatewayError("API_URL_INVALID", "Address must start with https:// and name a host")
    if parts.username or parts.password:
        raise HttpGatewayError("API_URL_INVALID", "Address must not contain a user or password; use the credential")
    host = parts.hostname.lower()
    port = parts.port
    names = {host} | ({f"{host}:{port}"} if port else set())
    if not names & settings.allowed_hosts:
        raise HttpGatewayError("API_HOST_NOT_ALLOWED",
                               f"Host {host} is not in the allowed list of the server (DWH_HTTP_ALLOWED_HOSTS)")
    if parts.scheme == "http" and not names & settings.plain_hosts:
        raise HttpGatewayError("API_PLAIN_HTTP", f"Host {host}: only https is allowed")
    return parts


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None  # the 3xx answer reaches the caller as an HTTPError


def build_url(url: str, params: Sequence[tuple[str, str]]) -> str:
    if not params:
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True) + list(params)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))


def _retry_after(value: str | None) -> float | None:
    if value and value.strip().isdigit():
        return float(value.strip())
    return None


def get(url: str, params: Sequence[tuple[str, str]] = (), headers: Mapping[str, str] | None = None, *,
        settings: GatewaySettings | None = None, timeout: float = DEFAULT_TIMEOUT, opener=None) -> HttpResponse:
    """One GET. Non-2xx answers raise HttpGatewayError with code HTTP_<status> (Retry-After kept)."""
    settings = settings or GatewaySettings.from_env()
    full = build_url(url, params)
    check_url(full, settings)
    request = urllib.request.Request(full, method="GET")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    request.add_header("Accept-Encoding", "identity")
    if opener is None:
        context = ssl.create_default_context(cafile=settings.ca_file) if settings.ca_file else ssl.create_default_context()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                             urllib.request.HTTPSHandler(context=context))
    shown = shown_url(full)
    try:
        with opener.open(request, timeout=timeout) as resp:
            body = resp.read(settings.max_bytes + 1)
            status, answer_headers, final = resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.geturl()
    except urllib.error.HTTPError as exc:
        status = exc.code
        retry = _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        exc.close()
        kind = "redirect" if 300 <= status < 400 else "answer"
        raise HttpGatewayError(f"HTTP_{status}", f"{shown}: HTTP {status} ({kind})", status, retry) from None
    except (socket.timeout, TimeoutError):
        raise HttpGatewayError("API_TIMEOUT", f"{shown}: no answer in {timeout:.0f} s") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise HttpGatewayError("API_TLS", f"{shown}: TLS failed ({getattr(reason, 'reason', None) or reason})") from None
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise HttpGatewayError("API_TIMEOUT", f"{shown}: no answer in {timeout:.0f} s") from None
        raise HttpGatewayError("API_UNREACHABLE", f"{shown}: {reason}") from None
    except (ssl.SSLError, ConnectionError, OSError) as exc:
        raise HttpGatewayError("API_UNREACHABLE", f"{shown}: {type(exc).__name__}") from None
    if len(body) > settings.max_bytes:
        raise HttpGatewayError("API_RESPONSE_TOO_LARGE", f"{shown}: answer larger than {settings.max_bytes} bytes")
    if urllib.parse.urlsplit(final).netloc != urllib.parse.urlsplit(full).netloc:
        raise HttpGatewayError("API_REDIRECTED", f"{shown}: answer came from another host")
    return HttpResponse(status=status, headers=answer_headers, body=body, url=full)
