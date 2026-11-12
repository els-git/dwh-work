"""Read-only network check of the DWH server (I16 step 5). Changes nothing, sends no queries.

  sudo python3 dwh_check_network.py [--target host:port ...] [--cert /etc/dwh/tls/dwh.crt --key ...]

Checks: every source (DNS name and TCP connection), local PostgreSQL (TCP 127.0.0.1:5432),
time synchronisation, the HTTPS certificate (valid > 30 days, matches its key, name), and that
the internet is closed (a connection to a public address must fail). NETWORK_RESULT: PASS / FAIL.
Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path

DEFAULT_TARGETS = ("10.30.58.64:5432", "dev-db.vtbdc.tech:54320")
PUBLIC_PROBES = ("1.1.1.1:443", "github.com:443")


def parse_target(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"Not host:port: {text}")
    return host, int(port)


def tcp(host: str, port: int, timeout: float = 5.0) -> str | None:
    """None when connected, otherwise the reason."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return type(exc).__name__ + (f" {exc.strerror}" if exc.strerror else "")


def check_source(target: str) -> tuple[bool, str]:
    host, port = parse_target(target)
    try:
        address = socket.gethostbyname(host)
    except OSError:
        return False, f"source {target}: DNS name not resolved"
    reason = tcp(host, port)
    return reason is None, f"source {target} ({address}): " + ("TCP OK" if reason is None else f"no connection, {reason}")


def check_time(run=subprocess.run) -> tuple[bool, str]:
    try:
        out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, text=True, check=False).stdout.strip()
    except OSError:
        return False, "time: timedatectl not available"
    return out == "yes", f"time synchronised: {out or 'unknown'}"


def check_cert(cert: Path, key: Path, name: str | None, run=subprocess.run) -> list[tuple[bool, str]]:
    if not cert.is_file() or not key.is_file():
        return [(False, f"certificate: {cert} or {key} missing")]
    results = []
    valid = run(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", str(30 * 86400)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    end = run(["openssl", "x509", "-in", str(cert), "-noout", "-enddate"], stdout=subprocess.PIPE,
              stderr=subprocess.PIPE, text=True, check=False).stdout.strip()
    results.append((valid.returncode == 0, f"certificate valid more than 30 days ({end})"))
    pub_cert = run(["openssl", "x509", "-in", str(cert), "-noout", "-pubkey"], stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, text=True, check=False).stdout
    pub_key = run(["openssl", "pkey", "-in", str(key), "-pubout"], stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, text=True, check=False).stdout
    results.append((bool(pub_cert) and pub_cert == pub_key, "certificate matches its private key"))
    if name:
        ok = run(["openssl", "x509", "-in", str(cert), "-noout", "-checkhost", name], stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True, check=False).stdout
        results.append(("does match" in ok, f"certificate is for {name}"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", action="append", help="source host:port (repeat); default: CRM and OPERATION_COLLECTOR")
    parser.add_argument("--cert", default="/etc/dwh/tls/dwh.crt")
    parser.add_argument("--key", default="/etc/dwh/tls/dwh.key")
    parser.add_argument("--name", help="DNS name users open (checked against the certificate)")
    parser.add_argument("--skip-cert", action="store_true")
    args = parser.parse_args(argv)
    checks: list[tuple[bool, str]] = []
    for target in args.target or DEFAULT_TARGETS:
        try:
            checks.append(check_source(target))
        except ValueError as exc:
            checks.append((False, str(exc)))
    reason = tcp("127.0.0.1", 5432, 3)
    checks.append((reason is None, "local PostgreSQL 127.0.0.1:5432: " + ("OK" if reason is None else reason)))
    checks.append(check_time())
    if not args.skip_cert:
        checks.extend(check_cert(Path(args.cert), Path(args.key), args.name))
    for probe in PUBLIC_PROBES:
        host, port = parse_target(probe)
        open_ = tcp(host, port, 3) is None
        checks.append((not open_, f"internet closed ({probe}): " + ("NO, connection succeeded" if open_ else "yes")))
    for ok, text in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {text}")
    failed = [text for ok, text in checks if not ok]
    print(f"NETWORK_RESULT: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)} of {len(checks)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
