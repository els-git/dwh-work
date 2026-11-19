"""I20: a tiny LDAP server for tests of directory_login (StartTLS / LDAPS, simple bind, one-level search answer).

Not a directory: it knows a service account and a few users and answers exactly the requests directory_login sends.
Records every bind (name only) so tests can prove that no password was sent over an untrusted connection.
"""
from __future__ import annotations

import datetime
import socket
import ssl
import tempfile
import threading
import uuid
from pathlib import Path

from dwh_api.directory_login import _integer, _octets, _tlv, parse_message, read_tlv


def make_certificate(directory: Path, name: str = "localhost") -> tuple[Path, Path, bytes]:
    """Self-signed certificate (cert file, key file, DER) for the fake controller."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path, cert.public_bytes(serialization.Encoding.DER)


def _result(tag: int, code: int, diagnostic: str = "") -> bytes:
    return _tlv(tag, _integer(code, 0x0A) + _octets("") + _octets(diagnostic))


def _answer(message_id: int, op: bytes) -> bytes:
    return _tlv(0x30, _integer(message_id) + op)


class FakeDirectory:
    """users: {sAMAccountName: {"dn", "password", "guid", "uac", "bind_error"(optional diagnostic for 49)}}."""

    def __init__(self, users: dict, service_dn: str = "svc@vtbaze.local", service_password: str = "svc-pass",
                 base_dns: tuple[str, ...] = ("OU=Users,DC=vtbaze,DC=local",), mode: str = "starttls"):
        self.users = users
        self.service_dn = service_dn
        self.service_password = service_password
        self.base_dns = base_dns
        self.mode = mode                 # starttls | ldaps | refuse_starttls
        self.binds: list[str] = []
        self.searches: list[str] = []
        self._tmp = tempfile.TemporaryDirectory()
        self.cert, self.key, self.der = make_certificate(Path(self._tmp.name))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(self.cert, self.key)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def fingerprint(self) -> str:
        import hashlib
        return hashlib.sha256(self.der).hexdigest().upper()

    def close(self) -> None:
        self._stop = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)   # wakes the accept() of the server thread
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self._tmp.cleanup()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn) -> None:
        buffer = b""
        identity = None
        try:
            if self.mode == "ldaps":
                conn = self.context.wrap_socket(conn, server_side=True)
            while True:
                try:
                    _, _, end = read_tlv(buffer, 0)
                except IndexError:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buffer += chunk
                    continue
                data, buffer = buffer[:end], buffer[end:]
                message_id, tag, op = parse_message(data)
                if tag == 0x77:                                   # StartTLS
                    if self.mode == "refuse_starttls":
                        conn.sendall(_answer(message_id, _result(0x78, 2, "unsupported")))
                        continue
                    conn.sendall(_answer(message_id, _result(0x78, 0)))
                    conn = self.context.wrap_socket(conn, server_side=True)
                elif tag == 0x60:                                 # bind
                    parts = []
                    pos = 0
                    while pos < len(op):
                        t, v, pos = read_tlv(op, pos)
                        parts.append(v)
                    name, password = parts[1].decode(), parts[2].decode()
                    self.binds.append(name)
                    code, diagnostic = 49, "80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 52e, v4563"
                    if name == self.service_dn and password == self.service_password:
                        code, diagnostic, identity = 0, "", "service"
                    for user in self.users.values():
                        if name == user["dn"]:
                            if user.get("bind_error"):
                                diagnostic = user["bind_error"]
                            elif password == user["password"]:
                                code, diagnostic, identity = 0, "", name
                    conn.sendall(_answer(message_id, _result(0x61, code, diagnostic)))
                elif tag == 0x63:                                 # search
                    pos = 0
                    fields = []
                    while pos < len(op):
                        t, v, pos = read_tlv(op, pos)
                        fields.append((t, v))
                    base = fields[0][1].decode()
                    filt = fields[6][1]
                    values = []
                    pos = 0
                    while pos < len(filt):
                        _, eq, pos = read_tlv(filt, pos)
                        _, attr, p2 = read_tlv(eq, 0)
                        _, val, _ = read_tlv(eq, p2)
                        values.append((attr.decode(), val.decode()))
                    username = dict(values).get("sAMAccountName", "")
                    self.searches.append(username)
                    if identity != "service":
                        conn.sendall(_answer(message_id, _result(0x65, 50, "insufficient access")))
                        continue
                    if base not in self.base_dns:
                        conn.sendall(_answer(message_id, _result(0x65, 32, "no such object")))
                        continue
                    user = self.users.get(username.lower())
                    if user is not None and user["dn"].endswith(base):
                        attrs = (_tlv(0x30, _octets("objectGUID") + _tlv(0x31, _octets(uuid.UUID(user["guid"]).bytes_le)))
                                 + _tlv(0x30, _octets("userAccountControl") + _tlv(0x31, _octets(str(user.get("uac", 512))))))
                        conn.sendall(_answer(message_id, _tlv(0x64, _octets(user["dn"]) + _tlv(0x30, attrs))))
                    conn.sendall(_answer(message_id, _result(0x65, 0)))
                elif tag == 0x42:                                 # unbind
                    return
        except (OSError, ssl.SSLError, ValueError, IndexError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass
