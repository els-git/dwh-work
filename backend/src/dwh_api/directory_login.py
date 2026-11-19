"""I20: password check of a domain user (LDAP / Active Directory) for the DWH login.

Plan docs/DWH_I20_LDAP_LOGIN_PLAN_APPROVED_20261119.md, decisions D1-D9. Standard library only.

  D1  Every connection is encrypted and the controller is trusted before any password is sent: StartTLS on a plain
      LDAP port (LDAPS on 636 / 3269) and the SHA-256 fingerprint of the certificate must be one of
      DWH_LDAP_PINS (or the chain is verified by DWH_LDAP_CA_FILE). A port without StartTLS is refused.
  D2  The user types "user", "VTBAZE\\user" or "user@vtbaze.local"; DWH looks for sAMAccountName=user in the base
      DNs with the service account, reads its DN, objectGUID and userAccountControl, then checks the password with
      a bind as that DN. The first controller of DWH_LDAP_SERVERS is used, the next ones only when it is
      unreachable. The password is never kept, logged or returned.
  D5  Disabled / locked / expired accounts get their own reason codes (for the journal only).
  D6  An unreachable directory (or a broken service account) is LDAP_UNAVAILABLE: login through the domain is not
      possible, local users are not affected.

Settings (/etc/dwh/dwh.env, written by tools/server/dwh_ldap_setup.py):
  DWH_LDAP_ENABLED      true to enable domain login
  DWH_LDAP_SERVERS      host:port[,host:port] (first = primary)
  DWH_LDAP_PINS         SHA-256 fingerprints of the controller certificates, comma separated
  DWH_LDAP_CA_FILE      optional PEM of the domain CA (instead of pins)
  DWH_LDAP_DOMAIN       vtbaze.local      DWH_LDAP_NETBIOS  VTBAZE
  DWH_LDAP_BASE_DNS     base DNs to search, separated by ';'
  DWH_LDAP_BIND_USER    login of the service account (UPN or DN)
  DWH_LDAP_TIMEOUT      seconds per network operation (default 5)
The service account password is the systemd credential dwh-ldap-bind ($CREDENTIALS_DIRECTORY), or, for tests
only, DWH_LDAP_BIND_PASSWORD_FILE.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import ssl
import uuid
from dataclasses import dataclass, field
from pathlib import Path

CREDENTIAL_NAME = "dwh-ldap-bind"
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Reasons (journal codes, decision D8). Only OK opens a session.
OK = "OK"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
LDAP_NOT_FOUND = "LDAP_NOT_FOUND"
LDAP_ACCOUNT_DISABLED = "LDAP_ACCOUNT_DISABLED"
LDAP_ACCOUNT_LOCKED = "LDAP_ACCOUNT_LOCKED"
LDAP_ACCOUNT_EXPIRED = "LDAP_ACCOUNT_EXPIRED"
LDAP_PASSWORD_EXPIRED = "LDAP_PASSWORD_EXPIRED"
LDAP_UNAVAILABLE = "LDAP_UNAVAILABLE"
LDAP_GUID_MISMATCH = "LDAP_GUID_MISMATCH"

# Active Directory sub-codes of invalidCredentials (49), "data 52e" in the diagnostic message.
AD_SUBCODES = {"525": LDAP_NOT_FOUND, "52e": INVALID_CREDENTIALS, "530": INVALID_CREDENTIALS,
               "531": INVALID_CREDENTIALS, "532": LDAP_PASSWORD_EXPIRED, "533": LDAP_ACCOUNT_DISABLED,
               "701": LDAP_ACCOUNT_EXPIRED, "773": LDAP_PASSWORD_EXPIRED, "775": LDAP_ACCOUNT_LOCKED}
UAC_ACCOUNTDISABLE = 0x0002
STARTTLS_OID = b"1.3.6.1.4.1.1466.20037"


class DirectoryError(Exception):
    """The directory cannot be used (network, TLS, trust, protocol, service account): LDAP_UNAVAILABLE."""


@dataclass
class Settings:
    servers: list[tuple[str, int]]
    pins: list[str]
    ca_file: str | None
    domain: str
    netbios: str
    base_dns: list[str]
    bind_user: str
    timeout: float = 5.0

    @property
    def problems(self) -> list[str]:
        out = []
        if not self.servers:
            out.append("DWH_LDAP_SERVERS is empty")
        if not self.pins and not self.ca_file:
            out.append("neither DWH_LDAP_PINS nor DWH_LDAP_CA_FILE is set")
        if not self.base_dns:
            out.append("DWH_LDAP_BASE_DNS is empty")
        if not self.bind_user:
            out.append("DWH_LDAP_BIND_USER is empty")
        return out


@dataclass
class Result:
    reason: str
    guid: str | None = None
    dn: str | None = None
    server: str | None = None
    details: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.reason == OK


def enabled() -> bool:
    return os.environ.get("DWH_LDAP_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _servers(text: str) -> list[tuple[str, int]]:
    out = []
    for item in (x.strip() for x in text.split(",")):
        if not item:
            continue
        host, _, port = item.rpartition(":")
        if not host or not port.isdigit():
            raise DirectoryError(f"DWH_LDAP_SERVERS: '{item}' is not host:port")
        out.append((host, int(port)))
    return out


def settings_from_env(env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        servers=_servers(env.get("DWH_LDAP_SERVERS", "")),
        pins=[normalize_pin(p) for p in env.get("DWH_LDAP_PINS", "").split(",") if p.strip()],
        ca_file=(env.get("DWH_LDAP_CA_FILE") or "").strip() or None,
        domain=env.get("DWH_LDAP_DOMAIN", "").strip().lower(),
        netbios=env.get("DWH_LDAP_NETBIOS", "").strip().upper(),
        base_dns=[d.strip() for d in env.get("DWH_LDAP_BASE_DNS", "").split(";") if d.strip()],
        bind_user=env.get("DWH_LDAP_BIND_USER", "").strip(),
        timeout=float(env.get("DWH_LDAP_TIMEOUT", "5") or 5),
    )


def service_password(env: dict | None = None) -> str:
    """Password of the service account: systemd credential (production) or a file named by
    DWH_LDAP_BIND_PASSWORD_FILE (tests). Raises DirectoryError when absent."""
    env = os.environ if env is None else env
    candidates = []
    if env.get("CREDENTIALS_DIRECTORY"):
        candidates.append(Path(env["CREDENTIALS_DIRECTORY"]) / CREDENTIAL_NAME)
    if env.get("DWH_LDAP_BIND_PASSWORD_FILE"):
        candidates.append(Path(env["DWH_LDAP_BIND_PASSWORD_FILE"]))
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError:
            continue
        if value:
            return value
    raise DirectoryError("the service account password (systemd credential dwh-ldap-bind) is not available")


def normalize_pin(pin: str) -> str:
    return pin.replace(":", "").replace(" ", "").strip().upper()


def normalize_username(login: str, settings: Settings) -> str | None:
    """'user', 'VTBAZE\\user' or 'user@vtbaze.local' -> 'user' (lower case). None for anything else: another
    domain, a DN, characters outside [A-Za-z0-9._-]."""
    text = (login or "").strip()
    if "\\" in text:
        prefix, _, text = text.partition("\\")
        if not settings.netbios or prefix.upper() != settings.netbios:
            return None
    elif "@" in text:
        text, _, suffix = text.rpartition("@")
        if not settings.domain or suffix.lower() != settings.domain:
            return None
    return text.lower() if USERNAME_RE.match(text) else None


# ------------------------------------------------------------------ BER (the subset of LDAPv3 used here)

def _length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _length(len(value)) + value


def _integer(n: int, tag: int = 0x02) -> bytes:
    return _tlv(tag, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True))


def _octets(value: str | bytes, tag: int = 0x04) -> bytes:
    return _tlv(tag, value.encode("utf-8") if isinstance(value, str) else value)


def _message(message_id: int, op: bytes) -> bytes:
    return _tlv(0x30, _integer(message_id) + op)


def bind_request(message_id: int, name: str, password: str) -> bytes:
    return _message(message_id, _tlv(0x60, _integer(3) + _octets(name) + _octets(password, 0x80)))


def starttls_request(message_id: int) -> bytes:
    return _message(message_id, _tlv(0x77, _octets(STARTTLS_OID, 0x80)))


def unbind_request(message_id: int) -> bytes:
    return _message(message_id, bytes([0x42, 0x00]))


def _equality(attribute: str, value: str) -> bytes:
    # equalityMatch [3]: the value is sent as bytes, never parsed as a filter string (no filter injection).
    return _tlv(0xA3, _octets(attribute) + _octets(value))


def search_request(message_id: int, base_dn: str, username: str, attributes: list[str],
                   size_limit: int = 2, time_limit: int = 10) -> bytes:
    filt = _tlv(0xA0, _equality("objectClass", "user") + _equality("sAMAccountName", username))
    body = (_octets(base_dn) + _integer(2, 0x0A) + _integer(0, 0x0A) + _integer(size_limit) + _integer(time_limit)
            + bytes([0x01, 0x01, 0x00]) + filt + _tlv(0x30, b"".join(_octets(a) for a in attributes)))
    return _message(message_id, _tlv(0x63, body))


def read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    if pos + 2 > len(data):
        raise IndexError("short")
    tag, first = data[pos], data[pos + 1]
    pos += 2
    if first & 0x80:
        count = first & 0x7F
        if pos + count > len(data):
            raise IndexError("short")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    else:
        length = first
    if pos + length > len(data):
        raise IndexError("short")
    return tag, data[pos:pos + length], pos + length


def _items(data: bytes):
    pos = 0
    while pos < len(data):
        tag, value, pos = read_tlv(data, pos)
        yield tag, value


def parse_message(data: bytes) -> tuple[int, int, bytes]:
    """(messageID, protocolOp tag, protocolOp content) of one LDAPMessage."""
    tag, message, _ = read_tlv(data, 0)
    if tag != 0x30:
        raise ValueError("not an LDAP message")
    parts = list(_items(message))
    if len(parts) < 2 or parts[0][0] != 0x02:
        raise ValueError("bad LDAP message")
    return int.from_bytes(parts[0][1], "big"), parts[1][0], parts[1][1]


def parse_result(op: bytes) -> tuple[int, str]:
    """(resultCode, diagnosticMessage) of an LDAPResult (bind, search done, extended)."""
    parts = list(_items(op))
    return int.from_bytes(parts[0][1], "big"), parts[2][1].decode("utf-8", "replace") if len(parts) > 2 else ""


def parse_entry(op: bytes) -> tuple[str, dict[str, list[bytes]]]:
    parts = list(_items(op))
    dn = parts[0][1].decode("utf-8", "replace")
    attributes: dict[str, list[bytes]] = {}
    for _, attribute in _items(parts[1][1]):
        pair = list(_items(attribute))
        name = pair[0][1].decode("utf-8", "replace").lower()
        attributes[name] = [value for _, value in _items(pair[1][1])] if len(pair) > 1 else []
    return dn, attributes


def describe_code(code: int, diagnostic: str) -> str:
    """Reason for a failed user bind (49 with the AD sub-code)."""
    if code == 49:
        match = re.search(r"data ([0-9a-f]{3})", diagnostic.lower())
        return AD_SUBCODES.get(match.group(1), INVALID_CREDENTIALS) if match else INVALID_CREDENTIALS
    return INVALID_CREDENTIALS


# ------------------------------------------------------------------ connection

class Connection:
    def __init__(self, sock, timeout: float):
        self.sock = sock
        self.buffer = b""
        self.next_id = 1
        self.sock.settimeout(timeout)

    def send(self, build) -> int:
        message_id = self.next_id
        self.next_id += 1
        self.sock.sendall(build(message_id))
        return message_id

    def receive(self) -> tuple[int, int, bytes]:
        while True:
            try:
                _, _, end = read_tlv(self.buffer, 0)
                data, self.buffer = self.buffer[:end], self.buffer[end:]
                return parse_message(data)
            except IndexError:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise DirectoryError("the controller closed the connection")
                self.buffer += chunk
                if len(self.buffer) > 4 * 1024 * 1024:
                    raise DirectoryError("answer of the controller is too large")

    def bind(self, name: str, password: str) -> tuple[int, str]:
        if not password:
            # An empty password would be an unauthenticated bind that AD reports as success.
            return 49, "empty password"
        message_id = self.send(lambda i: bind_request(i, name, password))
        got_id, tag, op = self.receive()
        if got_id != message_id or tag != 0x61:
            raise DirectoryError("unexpected answer to bind")
        return parse_result(op)

    def search(self, base_dn: str, username: str, attributes: list[str]) -> list[tuple[str, dict[str, list[bytes]]]]:
        message_id = self.send(lambda i: search_request(i, base_dn, username, attributes))
        entries = []
        while True:
            got_id, tag, op = self.receive()
            if got_id != message_id:
                raise DirectoryError("unexpected answer to search")
            if tag == 0x64:
                entries.append(parse_entry(op))
            elif tag == 0x73:          # search result reference (referral): not followed
                continue
            elif tag == 0x65:
                code, diagnostic = parse_result(op)
                if code == 32:         # noSuchObject: this base DN does not exist on the controller
                    raise DirectoryError(f"base DN not found: {base_dn}")
                if code not in (0, 4):  # 4 sizeLimitExceeded: more than one entry, reported as ambiguous
                    raise DirectoryError(f"search failed: result {code}")
                return entries
            else:
                raise DirectoryError("unexpected answer to search")

    def close(self) -> None:
        try:
            self.send(unbind_request)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _tls_context(settings: Settings) -> ssl.SSLContext:
    if settings.ca_file:
        context = ssl.create_default_context(cafile=settings.ca_file)
    else:
        # Trust comes from the fingerprint compared below, before anything is sent.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def connect(settings: Settings, host: str, port: int) -> Connection:
    """Encrypted, trusted connection to one controller. Raises DirectoryError."""
    try:
        raw = socket.create_connection((host, port), timeout=settings.timeout)
    except OSError as exc:
        raise DirectoryError(f"{host}:{port} unreachable: {type(exc).__name__}") from None
    try:
        if port not in (636, 3269):
            plain = Connection(raw, settings.timeout)
            message_id = plain.send(starttls_request)
            got_id, tag, op = plain.receive()
            if got_id != message_id or tag != 0x78 or parse_result(op)[0] != 0:
                raise DirectoryError(f"{host}:{port} refused StartTLS: no encryption, no login")
        tls = _tls_context(settings).wrap_socket(raw, server_hostname=host)
        der = tls.getpeercert(binary_form=True) or b""
        if not settings.ca_file and hashlib.sha256(der).hexdigest().upper() not in settings.pins:
            tls.close()
            raise DirectoryError(f"{host}:{port}: certificate fingerprint is not trusted")
        return Connection(tls, settings.timeout)
    except (OSError, ssl.SSLError, ValueError, IndexError) as exc:
        raw.close()
        raise DirectoryError(f"{host}:{port}: {type(exc).__name__}") from None
    except DirectoryError:
        raw.close()
        raise


def _guid(raw: bytes) -> str | None:
    return str(uuid.UUID(bytes_le=raw)) if len(raw) == 16 else None


def _int(values: list[bytes]) -> int:
    try:
        return int(values[0].decode()) if values else 0
    except ValueError:
        return 0


def check_user(conn: Connection, settings: Settings, password_of_service: str, username: str,
               password: str | None) -> Result:
    """Service bind, search in every base DN, account state, then (when password is given) the user bind."""
    code, _ = conn.bind(settings.bind_user, password_of_service)
    if code != 0:
        raise DirectoryError(f"service account bind failed (result {code})")
    entries = []
    for base_dn in settings.base_dns:
        entries += conn.search(base_dn, username, ["objectGUID", "userAccountControl"])
    if not entries:
        return Result(LDAP_NOT_FOUND)
    if len(entries) > 1:
        return Result(INVALID_CREDENTIALS, details={"note": "more than one directory account with this name"})
    dn, attributes = entries[0]
    guid = _guid((attributes.get("objectguid") or [b""])[0])
    uac = _int(attributes.get("useraccountcontrol", []))
    if guid is None:
        raise DirectoryError("the directory account has no objectGUID")
    if uac & UAC_ACCOUNTDISABLE:
        return Result(LDAP_ACCOUNT_DISABLED, guid=guid, dn=dn)
    # A lockout is not read from attributes (AD keeps lockoutTime after the lockout ends): the user bind reports it
    # (sub-code 775).
    if password is None:
        return Result(OK, guid=guid, dn=dn)
    code, diagnostic = conn.bind(dn, password)
    return Result(OK if code == 0 else describe_code(code, diagnostic), guid=guid, dn=dn)


def authenticate(login: str, password: str, settings: Settings | None = None,
                 service_secret: str | None = None) -> Result:
    """Checks a domain login and password. Never raises for a wrong password or account state; directory problems
    give LDAP_UNAVAILABLE (with the reason in details, without secrets)."""
    try:
        settings = settings or settings_from_env()
        if settings.problems:
            return Result(LDAP_UNAVAILABLE, details={"error": "; ".join(settings.problems)})
        username = normalize_username(login, settings)
        if username is None or not password:
            return Result(INVALID_CREDENTIALS)
        secret = service_secret if service_secret is not None else service_password()
    except DirectoryError as exc:
        return Result(LDAP_UNAVAILABLE, details={"error": str(exc)})
    errors = []
    for host, port in settings.servers:
        try:
            conn = connect(settings, host, port)
        except DirectoryError as exc:
            errors.append(str(exc))
            continue
        try:
            result = check_user(conn, settings, secret, username, password)
            result.server = f"{host}:{port}"
            return result
        except (DirectoryError, OSError, ValueError, IndexError) as exc:
            # A controller that answered but failed is not retried on the next one for this password.
            return Result(LDAP_UNAVAILABLE, server=f"{host}:{port}", details={"error": f"{type(exc).__name__}: {exc}"})
        finally:
            conn.close()
    return Result(LDAP_UNAVAILABLE, details={"error": "; ".join(errors)[:500]})
