r"""I20: domain login (LDAP / Active Directory) settings of the DWH server.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_ldap_setup.py \
      --server <dc1>:<port> --server <dc2>:<port> --pin <SHA-256 dc1> --pin <SHA-256 dc2> \
      --base-dn "OU=...,DC=vtbaze,DC=local" --base-dn "OU=...,DC=vtbaze,DC=local" \
      --domain vtbaze.local --netbios VTBAZE                    show what will change
  ... the same --apply                                         make the changes, check, then enable
  sudo python3 .../dwh_ldap_setup.py --check [--user <login>]   check only (optionally look up one user, no password)
  sudo python3 .../dwh_ldap_setup.py --disable                  turn domain login off (local login unchanged)

Plan docs/DWH_I20_LDAP_LOGIN_PLAN_APPROVED_20261119.md (D1, D7). Changes (each only if still needed):
  P1 settings      DWH_LDAP_* in /etc/dwh/dwh.env (controllers, fingerprints, base DNs, domain, login of the
                   service account); DWH_LDAP_ENABLED stays false until the check passes
  P2 password      of the service account: typed at the prompt (not shown), encrypted by systemd-creds into
                   /etc/dwh/ldap/dwh-ldap-bind.cred (name dwh-ldap-bind); never printed or written in clear text
  P3 drop-in       /etc/systemd/system/dwh-api.service.d/20-ldap.conf: LoadCredentialEncrypted for dwh-api only
  P4 restart       dwh-api
Then the check: every controller answers over StartTLS / LDAPS with a trusted certificate and the service account
binds and searches; with --user the account is looked up (found / disabled, first 8 characters of its objectGUID).
After a PASS DWH_LDAP_ENABLED=true is written and dwh-api restarted. The login of the service account is asked at
the prompt when it is not in dwh.env yet (or with --rotate-password). Prints LDAP_SETUP_RESULT: PASS / FAIL / PLAN.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "backend" / "src"))
import dwh_ai_setup as ai  # noqa: E402  (env file helpers, run)
from dwh_api import directory_login as dl  # noqa: E402

DWH_ENV = ai.DWH_ENV
CRED_DIR = Path("/etc/dwh/ldap")
CRED_FILE = CRED_DIR / f"{dl.CREDENTIAL_NAME}.cred"
DROPIN = Path("/etc/systemd/system/dwh-api.service.d/20-ldap.conf")
HEADER = "# I20 domain login (tools/server/dwh_ldap_setup.py)"


def dropin_text() -> str:
    return ("# I20 domain login: password of the service account for dwh-api only (tools/server/dwh_ldap_setup.py)\n"
            f"[Service]\nLoadCredentialEncrypted={dl.CREDENTIAL_NAME}:{CRED_FILE}\n")


def wanted_settings(args, env: dict[str, str]) -> dict[str, str]:
    values = {
        "DWH_LDAP_SERVERS": ",".join(args.server),
        "DWH_LDAP_PINS": ",".join(dl.normalize_pin(p) for p in args.pin),
        "DWH_LDAP_BASE_DNS": ";".join(args.base_dn),
        "DWH_LDAP_DOMAIN": args.domain.lower(),
        "DWH_LDAP_NETBIOS": args.netbios.upper(),
        "DWH_LDAP_TIMEOUT": str(args.timeout),
    }
    if env.get("DWH_LDAP_BIND_USER") and not args.rotate_password:
        values["DWH_LDAP_BIND_USER"] = env["DWH_LDAP_BIND_USER"]
    return values


def validate(args) -> list[str]:
    problems = []
    if not args.server:
        problems.append("give --server host:port (the primary controller first)")
    try:
        dl._servers(",".join(args.server))
    except dl.DirectoryError as exc:
        problems.append(str(exc))
    if not args.pin:
        problems.append("give --pin <SHA-256> for every controller (confirmed with the infrastructure team)")
    for pin in args.pin:
        if len(dl.normalize_pin(pin)) != 64 or any(c not in "0123456789ABCDEF" for c in dl.normalize_pin(pin)):
            problems.append(f"--pin is not a SHA-256 fingerprint (64 hex characters): {pin[:12]}...")
    if not args.base_dn:
        problems.append("give --base-dn for every folder (OU) with DWH users")
    if not args.domain or not args.netbios:
        problems.append("give --domain (vtbaze.local) and --netbios (VTBAZE)")
    return problems


def plan(args) -> list[tuple[str, str]]:
    env = ai.read_env(DWH_ENV)
    wanted = wanted_settings(args, env)
    steps = []
    if any(env.get(k) != v for k, v in wanted.items()) or "DWH_LDAP_BIND_USER" not in wanted:
        steps.append(("P1", f"{DWH_ENV}: DWH_LDAP_* ({len(args.server)} controllers, {len(args.base_dn)} base DNs); "
                            "login of the service account asked at the prompt if missing"))
    if not CRED_FILE.is_file() or args.rotate_password:
        steps.append(("P2", f"password of the service account (prompt, not shown) -> systemd-creds {CRED_FILE}"))
    if not DROPIN.is_file() or DROPIN.read_text() != dropin_text():
        steps.append(("P3", f"{DROPIN}: LoadCredentialEncrypted for dwh-api"))
    if steps:
        steps.append(("P4", "restart dwh-api"))
    return steps


def write_env(updates: dict[str, str]) -> None:
    stat = DWH_ENV.stat()
    tmp = DWH_ENV.with_suffix(".env.new")
    tmp.write_text(ai.env_with(DWH_ENV.read_text(), updates, HEADER))
    os.chown(tmp, stat.st_uid, stat.st_gid)
    tmp.chmod(stat.st_mode & 0o777)
    tmp.replace(DWH_ENV)


def apply(args, steps: list[tuple[str, str]]) -> None:
    codes = {code for code, _ in steps}
    if "P1" in codes:
        updates = wanted_settings(args, ai.read_env(DWH_ENV))
        if "DWH_LDAP_BIND_USER" not in updates:
            login = input("login of the service account (user@vtbaze.local or VTBAZE\\user): ").strip()
            if not login:
                raise RuntimeError("empty login of the service account")
            updates["DWH_LDAP_BIND_USER"] = login
        # Domain login is enabled only after the check below passes.
        updates["DWH_LDAP_ENABLED"] = "false"
        write_env(updates)
    if "P2" in codes:
        password = getpass.getpass("password of the service account (not shown): ")
        if not password:
            raise RuntimeError("empty password (an empty password would be an anonymous bind)")
        CRED_DIR.mkdir(mode=0o700, exist_ok=True)
        CRED_DIR.chmod(0o700)
        # The password goes through standard input only: not in the process list, not in a clear-text file.
        ai.run(["systemd-creds", "encrypt", f"--name={dl.CREDENTIAL_NAME}", "-", str(CRED_FILE)], stdin=password)
        password = ""
        CRED_FILE.chmod(0o600)
    if "P3" in codes:
        DROPIN.parent.mkdir(parents=True, exist_ok=True)
        DROPIN.write_text(dropin_text())
        DROPIN.chmod(0o644)
        ai.run(["systemctl", "daemon-reload"])
    if "P4" in codes:
        ai.run(["systemctl", "restart", "dwh-api.service"])


def service_secret() -> str:
    result = ai.run(["systemd-creds", "decrypt", f"--name={dl.CREDENTIAL_NAME}", str(CRED_FILE), "-"], check=False)
    if result.returncode != 0 or not result.stdout:
        raise dl.DirectoryError("the credential of the service account cannot be decrypted")
    return result.stdout.rstrip("\r\n")


def check(user: str | None) -> list[str]:
    env = ai.read_env(DWH_ENV)
    try:
        settings = dl.settings_from_env(env)
    except dl.DirectoryError as exc:
        return [str(exc)]
    if settings.problems:
        return settings.problems
    try:
        secret = service_secret()
    except dl.DirectoryError as exc:
        return [str(exc)]
    problems = []
    username = dl.normalize_username(user, settings) if user else None
    if user and username is None:
        problems.append("--user: not a login of this domain")
    for host, port in settings.servers:
        try:
            conn = dl.connect(settings, host, port)
        except dl.DirectoryError as exc:
            problems.append(str(exc))
            print(f"{host}:{port}: FAIL {exc}")
            continue
        try:
            if username:
                result = dl.check_user(conn, settings, secret, username, None)
                guid = (result.guid or "")[:8]
                print(f"{host}:{port}: PASS encrypted, trusted, service account OK; user {username}: {result.reason}"
                      + (f", objectGUID {guid}..." if guid else ""))
            else:
                code, _ = conn.bind(settings.bind_user, secret)
                if code != 0:
                    raise dl.DirectoryError(f"service account bind failed (result {code})")
                for base_dn in settings.base_dns:
                    conn.search(base_dn, "dwh-setup-check-no-such-user", ["objectGUID"])
                print(f"{host}:{port}: PASS encrypted, trusted, service account binds, {len(settings.base_dns)} base DNs found")
        except (dl.DirectoryError, OSError, ValueError, IndexError) as exc:
            problems.append(f"{host}:{port}: {exc}")
            print(f"{host}:{port}: FAIL {exc}")
        finally:
            conn.close()
    secret = ""
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="append", default=[], help="host:port, the primary controller first")
    parser.add_argument("--pin", action="append", default=[], help="SHA-256 of a controller certificate")
    parser.add_argument("--base-dn", action="append", default=[], help="folder (OU) with DWH users")
    parser.add_argument("--domain", default="")
    parser.add_argument("--netbios", default="")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--rotate-password", action="store_true", help="ask the login and password again")
    parser.add_argument("--user", default=None, help="with --check: look up this login (no password)")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("LDAP_SETUP_RESULT: FAIL (run with sudo)")
        return 1
    if args.disable:
        write_env({"DWH_LDAP_ENABLED": "false"})
        ai.run(["systemctl", "restart", "dwh-api.service"])
        print("domain login disabled (DWH_LDAP_ENABLED=false), dwh-api restarted; local login unchanged")
        print("LDAP_SETUP_RESULT: PASS")
        return 0
    if not args.check:
        problems = validate(args)
        if problems:
            print("LDAP_SETUP_RESULT: FAIL (" + "; ".join(problems) + ")")
            return 1
        steps = plan(args)
        for code, text in steps:
            print(f"{code} {text}")
        if not steps:
            print("nothing to change")
        if not args.apply:
            print("LDAP_SETUP_RESULT: PLAN (run with --apply to make these changes)")
            return 0
        try:
            apply(args, steps)
        except (RuntimeError, OSError) as exc:
            print(f"LDAP_SETUP_RESULT: FAIL ({exc})")
            return 1
        print("changes applied")
    problems = check(args.user)
    if problems:
        print("LDAP_SETUP_RESULT: FAIL (" + "; ".join(problems) + ")")
        return 1
    if args.apply and ai.read_env(DWH_ENV).get("DWH_LDAP_ENABLED") != "true":
        write_env({"DWH_LDAP_ENABLED": "true"})
        ai.run(["systemctl", "restart", "dwh-api.service"])
        print("domain login enabled (DWH_LDAP_ENABLED=true), dwh-api restarted")
    print("LDAP_SETUP_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
