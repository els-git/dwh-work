"""ETL tasks by hand on the DWH server (I16) - the server counterpart of tools/run_etl.ps1.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py check-crm
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py run-object --object-id 16 [--stage ODS] [--full]
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py plan-snapshot --snapshot-date 2026-11-04

The task runs as a short systemd unit under the user dwh, with the same settings
(/etc/dwh/dwh.env, /etc/dwh/keyring.env) and the same encrypted master keys
(LoadCredentialEncrypted= lines of the keyring drop-in) as the DWH services: master keys
are never readable outside such a unit. Output and exit code are those of the task.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TASKS = {
    "check-crm": ["check_crm.py"],
    "check-dwh": ["check_dwh.py"],
    "check": ["check_crm.py", "check_dwh.py"],
    "capture-source-metadata": ["capture_source_metadata.py"],
    "plan-snapshot": ["plan_snapshot.py"],
    "extract-snapshot": ["extract_snapshot.py"],
    "load-snapshot": ["load_snapshot.py"],
    "validate-stg": ["validate_stg.py"],
    "transform-ods": ["transform_ods.py"],
    "refresh-marts": ["refresh_marts.py"],
    "run-dq": ["run_dq.py"],
    "audit-mappings": ["audit_mappings.py"],
    "load-mapping-catalog": ["load_mapping_catalog.py"],
    "run-object": ["run_object.py"],
}

ROOT = Path("/opt/dwh/current")
ENV_FILE = Path("/etc/dwh/dwh.env")
KEYRING_ENV = Path("/etc/dwh/keyring.env")
DROPIN = Path("/etc/systemd/system/dwh-.service.d/10-keyring.conf")
CREDENTIAL_RE = re.compile(r"^LoadCredentialEncrypted=(dwh-key\.[A-Za-z0-9_.-]{1,64}):(/[^\s]+\.cred)$")


def credentials(dropin: Path) -> list[str]:
    if not dropin.is_file():
        return []
    return [line.strip() for line in dropin.read_text().splitlines() if CREDENTIAL_RE.match(line.strip())]


def command(script: str, *, env: dict[str, str], root: Path = ROOT, dropin: Path = DROPIN,
            env_file: Path = ENV_FILE, keyring_env: Path = KEYRING_ENV) -> list[str]:
    src = root / "src"
    cmd = ["systemd-run", "--wait", "--pipe", "--collect", "--quiet", "--uid=dwh", "--gid=dwh",
           f"--working-directory={src}", f"--property=EnvironmentFile={env_file}"]
    if keyring_env.is_file():
        cmd.append(f"--property=EnvironmentFile={keyring_env}")
    cmd += [f"--property={line}" for line in credentials(dropin)]
    cmd += ["--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
            "--property=ReadWritePaths=/var/lib/dwh /var/log/dwh", "--property=PrivateTmp=yes"]
    base_env = {"DWH_ENV_FILE": str(env_file), "PYTHONPATH": f"{src}/backend/src:{src}/etl/src",
                "PYTHONUNBUFFERED": "1"}
    for name, value in {**base_env, **env}.items():
        cmd.append(f"--setenv={name}={value}")
    cmd += [str(root / "venv" / "bin" / "python"), str(src / "etl" / "src" / script)]
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=sorted(TASKS))
    parser.add_argument("--object-id", type=int, default=0)
    parser.add_argument("--stage", choices=("ALL", "ODS"), default="ALL")
    parser.add_argument("--full", action="store_true", help="run-object: read the whole object once")
    parser.add_argument("--snapshot-date", help="plan-snapshot: YYYY-MM-DD")
    args = parser.parse_args(argv)
    env: dict[str, str] = {}
    if args.task == "run-object":
        if args.object_id <= 0:
            print("STOP: --object-id <source object id> is required for run-object")
            return 2
        env.update(DWH_RUN_OBJECT_ID=str(args.object_id), DWH_RUN_OBJECT_STAGE=args.stage,
                   DWH_RUN_OBJECT_FULL="1" if args.full else "")
    if args.snapshot_date:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.snapshot_date):
            print("STOP: --snapshot-date must be YYYY-MM-DD")
            return 2
        env["SNAPSHOT_DATE"] = args.snapshot_date
    for script in TASKS[args.task]:
        print(f"\n=== RUNNING {script} ===", flush=True)
        code = subprocess.run(command(script, env=env)).returncode
        if code != 0:
            print(f"STOP: {script} failed (exit code {code})")
            return code
        print(f"PASSED: {script}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
