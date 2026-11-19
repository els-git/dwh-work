"""I20 preparation: check that the DWH server can reach the domain controller over LDAPS (read-only, no changes).

  python3 /srv/dwh/incoming/dwh_ldap_check.py --host dc01.bank.local
  python3 /srv/dwh/incoming/dwh_ldap_check.py --host dc01.bank.local --ca-file /srv/dwh/incoming/bank-ca.pem
  python3 /srv/dwh/incoming/dwh_ldap_check.py --host dc01.bank.local --ca-file ... --bind

Steps (each prints PASS / FAIL with the reason):
  1 DNS        the name resolves (addresses printed)
  2 TCP        port 636 (LDAPS) answers; 389 (LDAP) is reported for information only
  3 TLS        handshake with certificate verification: against --ca-file (the domain CA) or the system store;
               subject, issuer, validity and names of the certificate; the name must match --host
  4 BIND       only with --bind: simple bind of the integration account over the verified TLS connection.
               The login (user@domain or DN) and the password are typed at the prompt; the password is not
               shown, not printed, not written anywhere and not kept after the check.
Prints LDAP_CHECK_RESULT: PASS / FAIL. Uses the system python3 only (standard library).
"""
from __future__ import annotations

import argparse
import getpass
import socket
import ssl
import sys
from datetime import datetime, timezone

# Active Directory adds a sub-code to invalidCredentials (49) in the diagnostic message: "data 52e" etc.
AD_SUBCODES = {
    "525": "user not found",
    "52e": "wrong password",
    "530": "logon not permitted at this time",
    "531": "logon not permitted at this workstation",
    "532": "password expired",
    "533": "account disabled",
    "701": "account expired",
    "773": "user must change password",
    "775": "account locked out",
}
RESULT_CODES = {0: "success", 1: "operationsError", 7: "authMethodNotSupported", 8: "strongerAuthRequired",
                13: "confidentialityRequired", 32: "noSuchObject", 34: "invalidDNSyntax", 48: "inappropriateAuthentication",
                49: "invalidCredentials", 50: "insufficientAccessRights", 51: "busy", 52: "unavailable", 53: "unwillingToPerform"}


# ------------------------------------------------------------------ minimal BER for the LDAP simple bind

def _length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _length(len(value)) + value


def _integer(n: int) -> bytes:
    return _tlv(0x02, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True))


def bind_request(message_id: int, name: str, password: str) -> bytes:
    """LDAPMessage { messageID, BindRequest [APPLICATION 0] { version 3, name, simple [0] password } }."""
    body = _integer(3) + _tlv(0x04, name.encode("utf-8")) + _tlv(0x80, password.encode("utf-8"))
    return _tlv(0x30, _integer(message_id) + _tlv(0x60, body))


def _read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    tag = data[pos]
    first = data[pos + 1]
    pos += 2
    if first & 0x80:
        count = first & 0x7F
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    else:
        length = first
    return tag, data[pos:pos + length], pos + length


def parse_bind_response(data: bytes) -> tuple[int, str]:
    """(resultCode, diagnosticMessage) of a BindResponse [APPLICATION 1]."""
    tag, message, _ = _read_tlv(data, 0)
    if tag != 0x30:
        raise ValueError("not an LDAP message")
    _, _, pos = _read_tlv(message, 0)              # messageID
    tag, response, _ = _read_tlv(message, pos)
    if tag != 0x61:
        raise ValueError("not a bind response")
    _, code, pos = _read_tlv(response, 0)          # resultCode ENUMERATED
    _, _, pos = _read_tlv(response, pos)           # matchedDN
    _, diagnostic, _ = _read_tlv(response, pos)    # diagnosticMessage
    return int.from_bytes(code, "big"), diagnostic.decode("utf-8", "replace")


def _read_message(sock: ssl.SSLSocket) -> bytes:
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) >= 2:
            try:
                _, _, end = _read_tlv(data, 0)
                if end <= len(data):
                    return data[:end]
            except IndexError:
                pass
    return data


def describe_bind(code: int, diagnostic: str) -> str:
    text = f"{code} {RESULT_CODES.get(code, 'other')}"
    lower = diagnostic.lower()
    for sub, meaning in AD_SUBCODES.items():
        if f"data {sub}" in lower:
            text += f" (AD: {meaning})"
    return text


# ------------------------------------------------------------------ checks

def tls_context(ca_file: str | None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="domain controller name (as in its certificate)")
    parser.add_argument("--port", type=int, default=636)
    parser.add_argument("--ca-file", default=None, help="PEM file of the domain CA (from the infrastructure team)")
    parser.add_argument("--bind", action="store_true", help="also test the integration account (prompts)")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    problems: list[str] = []

    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM)})
        print(f"1 DNS  PASS  {args.host} -> {', '.join(addresses)}")
    except OSError as exc:
        print(f"1 DNS  FAIL  {args.host}: {exc}")
        print("LDAP_CHECK_RESULT: FAIL")
        return 1

    for port, required in ((args.port, True), (389, False)):
        try:
            with socket.create_connection((args.host, port), timeout=args.timeout):
                print(f"2 TCP  PASS  port {port} answers")
        except OSError as exc:
            print(f"2 TCP  {'FAIL' if required else 'INFO'}  port {port}: {exc}")
            if required:
                problems.append(f"port {port} closed or filtered from this server")
    if problems:
        print("LDAP_CHECK_RESULT: FAIL (" + "; ".join(problems) + ")")
        return 1

    try:
        context = tls_context(args.ca_file)
        raw = socket.create_connection((args.host, args.port), timeout=args.timeout)
        tls = context.wrap_socket(raw, server_hostname=args.host)
    except (OSError, ssl.SSLError) as exc:
        reason = str(exc)
        hint = ("; give the domain CA with --ca-file" if "CERTIFICATE_VERIFY_FAILED" in reason and not args.ca_file
                else "; the name in the certificate differs from --host" if "match" in reason.lower() else "")
        print(f"3 TLS  FAIL  {reason}{hint}")
        print("LDAP_CHECK_RESULT: FAIL")
        return 1
    with tls:
        cert = tls.getpeercert() or {}
        subject = ", ".join("=".join(x) for part in cert.get("subject", ()) for x in part)
        issuer = ", ".join("=".join(x) for part in cert.get("issuer", ()) for x in part)
        names = ", ".join(v for k, v in cert.get("subjectAltName", ()) if k == "DNS")
        not_after = cert.get("notAfter", "")
        days = None
        if not_after:
            expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc)
            days = (expires - datetime.now(timezone.utc)).days
        print(f"3 TLS  PASS  {tls.version()}; subject: {subject}; issuer: {issuer}; names: {names or '-'}; "
              f"valid until {not_after}" + (f" ({days} days)" if days is not None else ""))
        if days is not None and days < 30:
            problems.append(f"the controller certificate expires in {days} days")

        if args.bind:
            login = input("integration login (user@domain or DN): ").strip()
            password = getpass.getpass("password (not shown): ")
            if not login or not password:
                print("4 BIND FAIL  empty login or password (an empty password would be an anonymous bind)")
                problems.append("bind not tested")
            else:
                try:
                    tls.sendall(bind_request(1, login, password))
                    code, diagnostic = parse_bind_response(_read_message(tls))
                    print(f"4 BIND {'PASS' if code == 0 else 'FAIL'}  {describe_bind(code, diagnostic)}")
                    if code != 0:
                        problems.append("the integration account cannot bind")
                except (OSError, ValueError, IndexError) as exc:
                    print(f"4 BIND FAIL  {type(exc).__name__}: no valid answer from the controller")
                    problems.append("bind failed")
                finally:
                    password = ""
    print("LDAP_CHECK_RESULT: " + ("PASS" if not problems else "FAIL (" + "; ".join(problems) + ")"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
