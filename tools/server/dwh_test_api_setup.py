"""I11-05: test API service over PROCESS_VIEWER public.services on the DWH server.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py             show what will change
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --apply     make the changes, then check
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --check     check only
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --show-token   print the token (for the DWH form)
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --remove    stop the service, close /pv-api/

Changes (each only if still needed):
  T1 /etc/dwh/test-api.env    database host/port/name/user, the password (asked on the server, hidden input,
                              never printed) and a random token (root, 0600)
  T2 dwh-test-api.service     from the release (deploy/systemd), enabled and (re)started; only 127.0.0.1:8090
  T3 nginx include            one line "include /etc/dwh/nginx-dwh.d/*.conf;" in the DWH server block of
                              /etc/nginx/sites-available/dwh.conf (copy kept next to it), file
                              /etc/dwh/nginx-dwh.d/pv-test-api.conf (location /pv-api/), nginx -t, reload;
                              on a failed nginx -t the previous files are put back
Check: /health, 401 without token, JSON page 1, XML, cursor pages, 429 simulation - through
https://<name>/pv-api/ with the CA of /etc/dwh/tls/ca.crt. Prints TEST_API_RESULT: PASS / FAIL / PLAN.
Uses the system python3 only (the service itself runs with the release venv).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parents[1]
UNIT_NAME = "dwh-test-api.service"
UNIT_SRC = RELEASE / "deploy" / "systemd" / UNIT_NAME
UNIT_DST = Path("/etc/systemd/system") / UNIT_NAME
SNIPPET_SRC = RELEASE / "deploy" / "nginx" / "pv-test-api.conf"
ENV_FILE = Path("/etc/dwh/test-api.env")
INCLUDE_DIR = Path("/etc/dwh/nginx-dwh.d")
NGINX_CONF = Path("/etc/nginx/sites-available/dwh.conf")
CA_FILE = Path("/etc/dwh/tls/ca.crt")
INCLUDE_LINE = "    include /etc/dwh/nginx-dwh.d/*.conf;"
ANCHOR = "    location /api/ {"
ENV_KEYS = ("PV_DB_HOST", "PV_DB_PORT", "PV_DB_NAME", "PV_DB_USER", "PV_DB_PASSWORD", "PV_API_TOKEN", "PV_API_PORT")


class SetupError(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def env_text(values: dict[str, str]) -> str:
    lines = ["# I11-05: test API service (tools/server/dwh_test_api_setup.py). Root only; never print."]
    for key in ENV_KEYS:
        value = values.get(key, "")
        if any(c in value for c in "\n\r"):
            raise SetupError(f"{key}: line breaks are not allowed")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def with_include(conf: str) -> str:
    """nginx file with the include line before the /api/ location of the DWH server block (once)."""
    if INCLUDE_LINE.strip() in conf:
        return conf
    if conf.count(ANCHOR) != 1:
        raise SetupError(f"{NGINX_CONF}: expected exactly one '{ANCHOR.strip()}'")
    return conf.replace(ANCHOR, INCLUDE_LINE + "\n\n" + ANCHOR, 1)


def plan(args) -> list[tuple[str, str]]:
    steps = []
    env = read_env(args.env_file)
    wanted = {"PV_DB_HOST": args.db_host, "PV_DB_PORT": str(args.db_port), "PV_DB_NAME": args.db_name,
              "PV_DB_USER": args.db_user, "PV_API_PORT": str(args.port)}
    if not env.get("PV_DB_PASSWORD") or not env.get("PV_API_TOKEN") or any(env.get(k) != v for k, v in wanted.items()):
        steps.append(("T1", f"{args.env_file}: {args.db_user}@{args.db_host}:{args.db_port}/{args.db_name}, "
                            "password (asked now, hidden), token (kept if present)"))
    if not args.unit_dst.is_file() or args.unit_dst.read_bytes() != UNIT_SRC.read_bytes():
        steps.append(("T2", f"{args.unit_dst} from the release, enable, start"))
    snippet = args.include_dir / "pv-test-api.conf"
    conf = args.nginx_conf.read_text() if args.nginx_conf.is_file() else ""
    if not snippet.is_file() or snippet.read_bytes() != SNIPPET_SRC.read_bytes() or INCLUDE_LINE.strip() not in conf:
        steps.append(("T3", f"{snippet}, include line in {args.nginx_conf}, nginx -t, reload"))
    return steps


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SetupError(f"{' '.join(cmd)}: {(result.stderr or result.stdout).strip()[:400]}")
    return result


def write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def apply(args, steps: list[tuple[str, str]], ask_password=getpass.getpass) -> None:
    codes = {code for code, _ in steps}
    if "T1" in codes:
        env = read_env(args.env_file)
        password = ask_password(f"Password of {args.db_user} for {args.db_name} (input hidden): ")
        if not password:
            raise SetupError("empty password")
        env.update({"PV_DB_HOST": args.db_host, "PV_DB_PORT": str(args.db_port), "PV_DB_NAME": args.db_name,
                    "PV_DB_USER": args.db_user, "PV_DB_PASSWORD": password, "PV_API_PORT": str(args.port)})
        env.setdefault("PV_API_TOKEN", "")
        if len(env["PV_API_TOKEN"]) < 16:
            env["PV_API_TOKEN"] = secrets.token_urlsafe(32)
        write(args.env_file, env_text(env), 0o600)
        print(f"T1 done: {args.env_file} (password and token not shown)")
    if "T2" in codes:
        shutil.copyfile(UNIT_SRC, args.unit_dst)
        os.chmod(args.unit_dst, 0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", UNIT_NAME])
        print("T2 done: unit installed and enabled")
    if "T3" in codes:
        snippet = args.include_dir / "pv-test-api.conf"
        old_conf = args.nginx_conf.read_text()
        old_snippet = snippet.read_text() if snippet.is_file() else None
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(args.nginx_conf, args.nginx_conf.with_name(f"{args.nginx_conf.name}.pre-test-api.{stamp}"))
        args.include_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(args.include_dir, 0o755)
        write(snippet, SNIPPET_SRC.read_text(), 0o644)
        write(args.nginx_conf, with_include(old_conf), 0o644)
        try:
            run(["nginx", "-t"])
        except SetupError:
            write(args.nginx_conf, old_conf, 0o644)
            if old_snippet is None:
                snippet.unlink()
            else:
                write(snippet, old_snippet, 0o644)
            print("T3 failed: nginx -t refused, previous files put back")
            raise
        run(["systemctl", "reload", "nginx"])
        print("T3 done: /pv-api/ published, nginx reloaded")
    if codes & {"T1", "T2"} or not service_active():
        run(["systemctl", "restart", UNIT_NAME])
        print("service (re)started")


def service_active() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", UNIT_NAME]).returncode == 0


def fetch(url: str, token: str | None, context: ssl.SSLContext, timeout: float = 30) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, method="GET")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()
    except OSError as exc:  # no connection, TLS refused: a failed check, not a traceback
        return 0, {}, str(getattr(exc, "reason", exc)).encode()


def check(base: str, token: str, context: ssl.SSLContext, wait: int = 20, fetch_fn=fetch) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    status = 0
    for _ in range(max(wait, 1)):
        status, _, body = fetch_fn(f"{base}/health", None, context)
        if status == 200:
            break
        time.sleep(1)
    if status == 0:  # no answer at all: say why (TLS, connection) and stop
        reason = body.decode(errors="replace")[:300]
        return [(False, f"GET /health: no answer ({reason})")]
    results.append((status == 200, f"GET /health -> {status}"))
    status, _, _ = fetch_fn(f"{base}/v1/services?size=1", None, context)
    results.append((status == 401, f"no token -> {status} (expected 401)"))
    status, _, body = fetch_fn(f"{base}/v1/services?page=1&size=5", token, context)
    total = None
    if status == 200:
        doc = json.loads(body)
        total = doc["meta"]["total"]
        results.append((True, f"JSON page 1: {len(doc['data']['items'])} rows of {total}"))
    else:
        results.append((False, f"JSON page 1 -> {status}"))
    status, headers, body = fetch_fn(f"{base}/v1/services?page=1&size=5&format=xml", token, context)
    results.append((status == 200 and body.lstrip().startswith(b"<?xml") and b"<service>" in body,
                    f"XML page 1 -> {status}, {headers.get('Content-Type', '')}"))
    count, url, pages = 0, f"{base}/v1/services/cursor?limit=50", 0
    while url and pages < 1000:
        status, _, body = fetch_fn(url, token, context)
        if status != 200:
            break
        doc = json.loads(body)
        count += len(doc["items"])
        pages += 1
        url = f"{base}/v1/services/cursor?limit=50&cursor={doc['next_cursor']}" if doc["next_cursor"] else None
    results.append((status == 200 and total is not None and count == total,
                    f"cursor: {count} rows in {pages} pages (total {total})"))
    status, headers, _ = fetch_fn(f"{base}/v1/services?simulate=429", token, context)
    results.append((status == 429 and headers.get("Retry-After") == "2", f"simulate=429 -> {status}, Retry-After {headers.get('Retry-After')}"))
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--show-token", action="store_true")
    mode.add_argument("--remove", action="store_true")
    p.add_argument("--db-host", default="10.1.10.40")
    p.add_argument("--db-port", type=int, default=5432)
    p.add_argument("--db-name", default="process_viewer")
    p.add_argument("--db-user", default="dwh_reader")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--name", default="dwh.vtbaze.local", help="DNS name of the DWH server (nginx)")
    p.add_argument("--env-file", type=Path, default=ENV_FILE)
    p.add_argument("--unit-dst", type=Path, default=UNIT_DST)
    p.add_argument("--include-dir", type=Path, default=INCLUDE_DIR)
    p.add_argument("--nginx-conf", type=Path, default=NGINX_CONF)
    p.add_argument("--ca-file", type=Path, default=CA_FILE)
    args = p.parse_args(argv)
    if os.geteuid() != 0:
        print("Run with sudo (root).")
        return 2
    try:
        if args.show_token:
            token = read_env(args.env_file).get("PV_API_TOKEN")
            if not token:
                raise SetupError(f"no token in {args.env_file}; run --apply first")
            print(token)
            return 0
        if args.remove:
            subprocess.run(["systemctl", "disable", "--now", UNIT_NAME])
            snippet = args.include_dir / "pv-test-api.conf"
            if snippet.exists():
                snippet.unlink()
                run(["nginx", "-t"])
                run(["systemctl", "reload", "nginx"])
            print("TEST_API_RESULT: REMOVED (service stopped, /pv-api/ closed; settings file kept)")
            return 0
        steps = plan(args)
        if not args.apply and not args.check:
            for code, text in steps:
                print(f"{code}: {text}")
            if not steps:
                print("Nothing to change.")
            print("TEST_API_RESULT: PLAN (run with --apply)")
            return 0
        if args.apply and steps:
            apply(args, steps)
        token = read_env(args.env_file).get("PV_API_TOKEN", "")
        context = ssl.create_default_context(cafile=str(args.ca_file)) if args.ca_file.is_file() else ssl.create_default_context()
        results = check(f"https://{args.name}/pv-api", token, context)
    except SetupError as exc:
        print(f"ERROR: {exc}")
        print("TEST_API_RESULT: FAIL")
        return 1
    for ok, text in results:
        print(("PASS " if ok else "FAIL ") + text)
    passed = all(ok for ok, _ in results)
    print(f"TEST_API_RESULT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
