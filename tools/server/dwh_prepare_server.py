"""Preparation of the DWH server by ourselves (plan I16, section 4; findings A1, A3, A4, A8, A10).

  sudo python3 dwh_prepare_server.py            show what would change (nothing is changed)
  sudo python3 dwh_prepare_server.py --apply    make the changes

Done here (each item only when needed; running again changes nothing):
  A1  time zone Asia/Baku
  A4  packages python3-venv nginx git unzip ufw (apt; needs the internet or the internal mirror)
  A3  system user dwh (no login), folders of the runbook, /srv/dwh/incoming, /backup
  A8  PostgreSQL 17: conf.d/dwh.conf (memory by the size of the server, time zone Asia/Baku), restart
  A10 firewall ufw: incoming only 22 and 443, outgoing open; the default nginx site removed (port 80)
Not done here (administrators): separate disks (A2), access to CRM (A5), closing the internet (A6),
CPU/memory (A7), TPM (A11). SSH keys (A9) - separately, so that access is not lost.
Prints PREPARE_RESULT: PLAN / PASS / FAIL. Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGES = ("python3-venv", "nginx", "git", "unzip", "ufw")
PG_CONF = Path("/etc/postgresql/17/main/conf.d/dwh.conf")
NGINX_DEFAULT = Path("/etc/nginx/sites-enabled/default")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: exit code {result.returncode}: {result.stdout.strip()[-300:]}")
    return result


def output(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def memory_gb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal"):
            return max(1, round(int(line.split()[1]) / 1024 / 1024))
    return 1


def pg_conf_text(mem_gb: int) -> str:
    shared = max(1, mem_gb // 4)
    cache = max(1, mem_gb * 5 // 8)
    return ("# I16: DWH settings (tools/server/dwh_prepare_server.py), by the memory of the server: "
            f"{mem_gb} GB\n"
            f"shared_buffers = '{shared}GB'\n"
            f"effective_cache_size = '{cache}GB'\n"
            "work_mem = '32MB'\n"
            "maintenance_work_mem = '1GB'\n"
            "max_connections = 100\n"
            "password_encryption = 'scram-sha-256'\n"
            "timezone = 'Asia/Baku'\n"
            "log_timezone = 'Asia/Baku'\n"
            "listen_addresses = 'localhost'\n")


def admin_user() -> str:
    return os.environ.get("SUDO_USER") or "root"


def plan() -> list[tuple[str, list[list[str]]]]:
    """(description, commands) of every change still needed."""
    steps: list[tuple[str, list[list[str]]]] = []
    if output(["timedatectl", "show", "-p", "Timezone", "--value"]) != "Asia/Baku":
        steps.append(("A1 time zone -> Asia/Baku", [["timedatectl", "set-timezone", "Asia/Baku"]]))
    missing = [p for p in PACKAGES if "install ok installed" not in output(["dpkg-query", "-W", "-f=${Status}", p])]
    if missing:
        steps.append((f"A4 packages: {' '.join(missing)}",
                      [["apt-get", "update", "-q"],
                       ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "-q", *missing]]))
    try:
        pwd.getpwnam("dwh")
    except KeyError:
        steps.append(("A3 system user dwh (no login, home /opt/dwh)",
                      [["useradd", "--system", "--home-dir", "/opt/dwh", "--no-create-home",
                        "--shell", "/usr/sbin/nologin", "--user-group", "dwh"]]))
    folders = [
        ("/opt/dwh", "0755", "root", "root"), ("/opt/dwh/releases", "0755", "root", "root"),
        ("/etc/dwh", "0750", "root", "dwh"), ("/etc/dwh/tls", "0750", "root", "dwh"),
        ("/etc/dwh/keyring", "0700", "root", "root"),
        ("/var/lib/dwh", "0700", "dwh", "dwh"), ("/var/lib/dwh/secrets", "0700", "dwh", "dwh"),
        ("/var/lib/dwh/data", "0700", "dwh", "dwh"), ("/var/lib/dwh/runtime", "0700", "dwh", "dwh"),
        ("/var/log/dwh", "0750", "dwh", "dwh"),
        ("/backup", "0755", "root", "root"), ("/backup/dwh", "0700", "postgres", "postgres"),
        ("/srv/dwh", "0755", "root", "root"), ("/srv/dwh/incoming", "0750", admin_user(), "dwh"),
    ]
    todo = [f for f in folders if not Path(f[0]).is_dir()]
    if todo:
        steps.append(("A3 folders: " + ", ".join(f[0] for f in todo),
                      [["install", "-d", "-m", mode, "-o", owner, "-g", group, path] for path, mode, owner, group in todo]))
    wanted = pg_conf_text(memory_gb())
    if not PG_CONF.is_file() or PG_CONF.read_text() != wanted:
        steps.append((f"A8 PostgreSQL settings {PG_CONF} (memory {memory_gb()} GB) and restart",
                      [["write", str(PG_CONF), wanted], ["systemctl", "restart", "postgresql@17-main"]]))
    if NGINX_DEFAULT.is_symlink() or NGINX_DEFAULT.exists() or "nginx" in missing:
        steps.append(("A10 default nginx site off (port 80)", [["rm", "-f", str(NGINX_DEFAULT)],
                                                                ["systemctl", "reload-or-restart", "nginx"]]))
    if not output(["ufw", "status"]).startswith("Status: active"):
        steps.append(("A10 firewall: incoming 22 and 443 only",
                      [["ufw", "default", "deny", "incoming"], ["ufw", "default", "allow", "outgoing"],
                       ["ufw", "allow", "22/tcp"], ["ufw", "allow", "443/tcp"], ["ufw", "--force", "enable"]]))
    return steps


def execute(cmd: list[str]) -> None:
    if cmd[0] == "write":
        path = Path(cmd[1])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cmd[2])
        os.chmod(path, 0o644)
        return
    run(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="make the changes (without it: only show them)")
    args = parser.parse_args(argv)
    if os.name != "posix" or os.geteuid() != 0:
        print("PREPARE_RESULT: FAIL (run with sudo)")
        return 1
    if not shutil.which("timedatectl"):
        print("PREPARE_RESULT: FAIL (not a systemd server)")
        return 1
    steps = plan()
    if not steps:
        print("nothing to change")
        print("PREPARE_RESULT: PASS")
        return 0
    for description, commands in steps:
        print(f"- {description}")
        for cmd in commands:
            print("    " + (f"write {cmd[1]}" if cmd[0] == "write" else " ".join(cmd)))
    if not args.apply:
        print("PREPARE_RESULT: PLAN (nothing changed; run again with --apply)")
        return 0
    for description, commands in steps:
        try:
            for cmd in commands:
                execute(cmd)
        except (RuntimeError, OSError) as exc:
            print(f"FAIL  {description}: {exc}")
            print("PREPARE_RESULT: FAIL (the steps above this line are done; fix and run again)")
            return 1
        print(f"DONE  {description}")
    print("PREPARE_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
