"""I11 (B10): hosts the API sources of DWH may be called on (DWH_HTTP_ALLOWED_HOSTS in /etc/dwh/dwh.env).

  sudo python3 /opt/dwh/current/src/tools/server/dwh_http_hosts.py                     show the list
  sudo python3 /opt/dwh/current/src/tools/server/dwh_http_hosts.py --add dwh.vtbaze.local
  sudo python3 /opt/dwh/current/src/tools/server/dwh_http_hosts.py --remove dwh.vtbaze.local

A host is an exact DNS name or address, optionally with :port. After a change dwh-api and dwh-dispatcher are
restarted (they read the file at start). Only https is used; plain http needs DWH_HTTP_PLAIN_HOSTS, which this
tool does not change. Prints HTTP_HOSTS_RESULT: SHOWN / CHANGED / UNCHANGED / FAIL. Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path("/etc/dwh/dwh.env")
KEY = "DWH_HTTP_ALLOWED_HOSTS"
HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:\d{1,5})?$")


def current(text: str) -> list[str]:
    for line in text.splitlines():
        if line.startswith(KEY + "="):
            return [h for h in (x.strip().lower() for x in line.split("=", 1)[1].split(",")) if h]
    return []


def with_hosts(text: str, hosts: list[str]) -> str:
    line = f"{KEY}={','.join(hosts)}"
    lines = text.splitlines()
    for i, existing in enumerate(lines):
        if existing.startswith(KEY + "="):
            lines[i] = line
            break
    else:
        lines += ["# I11: hosts the API sources may be called on (tools/server/dwh_http_hosts.py)", line]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--add", metavar="HOST")
    mode.add_argument("--remove", metavar="HOST")
    p.add_argument("--env-file", type=Path, default=ENV_FILE)
    p.add_argument("--no-restart", action="store_true")
    args = p.parse_args(argv)
    if os.geteuid() != 0:
        print("Run with sudo (root).")
        return 2
    text = args.env_file.read_text() if args.env_file.is_file() else ""
    hosts = current(text)
    if not args.add and not args.remove:
        print("Allowed hosts: " + (", ".join(hosts) or "(none)"))
        print("HTTP_HOSTS_RESULT: SHOWN")
        return 0
    host = (args.add or args.remove).strip().lower()
    if not HOST.match(host):
        print(f"ERROR: {host!r} is not a host name (letters, digits, '.', '-', optional :port)")
        print("HTTP_HOSTS_RESULT: FAIL")
        return 1
    new = hosts + [host] if args.add and host not in hosts else [h for h in hosts if h != host] if args.remove else hosts
    if new == hosts:
        print("Allowed hosts: " + (", ".join(hosts) or "(none)"))
        print("HTTP_HOSTS_RESULT: UNCHANGED")
        return 0
    tmp = args.env_file.with_name(args.env_file.name + ".tmp")
    mode = args.env_file.stat().st_mode & 0o777 if args.env_file.exists() else 0o600
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(with_hosts(text, new))
    if args.env_file.exists():
        st = args.env_file.stat()
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, args.env_file)
    print("Allowed hosts: " + (", ".join(new) or "(none)"))
    if not args.no_restart:
        for unit in ("dwh-api", "dwh-dispatcher"):
            result = subprocess.run(["systemctl", "restart", unit], capture_output=True, text=True)
            print(f"restart {unit}: {'OK' if result.returncode == 0 else result.stderr.strip()[:200]}")
    print("HTTP_HOSTS_RESULT: CHANGED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
