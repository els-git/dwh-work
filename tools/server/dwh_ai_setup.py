"""I17 step 8: local AI model service and report role on the DWH server.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_ai_setup.py            show what will change
  sudo python3 /opt/dwh/current/src/tools/server/dwh_ai_setup.py --apply    make the changes, then check
  sudo python3 /opt/dwh/current/src/tools/server/dwh_ai_setup.py --check    check only

Changes (each only if still needed):
  L1 /etc/dwh/llm.env           model files, threads, context of llama-server (root, 0644)
  L2 dwh-llm.service            from the release (deploy/systemd), enabled; only 127.0.0.1, no network
  L3 kernel.numa_balancing = 0  /etc/sysctl.d/90-dwh-llm.conf (llama.cpp --numa distribute)
  L4 PostgreSQL memory          conf.d/dwh.conf with a small cache next to the model, PostgreSQL restart
  L5 dwh_ai_ro password         random, set by ALTER ROLE through psql standard input, written only to
                                /etc/dwh/dwh.env with DWH_AI_LLM_URL / DWH_AI_MODEL_LABEL (never printed)
  L6 services                   dwh-llm started; dwh-api and dwh-dispatcher restarted (new settings)
  L7 GRANT CONNECT              on database dwh to dwh_ai_ro (in dwh PUBLIC has no CONNECT)
Check: dwh-llm active and answering /health (waits for the model to load, --wait minutes); dwh_ai_ro logs in,
read-only, reads dm (row count only) and is refused on app. Prints AI_SETUP_RESULT: PASS / FAIL / PLAN.
Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dwh_prepare_server as prep  # noqa: E402

RELEASE = HERE.parents[1]
UNIT_SRC = RELEASE / "deploy" / "systemd" / "dwh-llm.service"
UNIT_DST = Path("/etc/systemd/system/dwh-llm.service")
LLM_ENV = Path("/etc/dwh/llm.env")
DWH_ENV = Path("/etc/dwh/dwh.env")
SYSCTL = Path("/etc/sysctl.d/90-dwh-llm.conf")
SYSCTL_TEXT = "# I17: llama.cpp --numa distribute on a server with several NUMA nodes\nkernel.numa_balancing = 0\n"
HEALTH = "http://127.0.0.1:8081/health"


def llm_env_text(args) -> str:
    model_dir = Path(args.model_dir)
    return ("# I17: local AI model of DWH (tools/server/dwh_ai_setup.py). Changing the model: edit, then\n"
            "#   sudo systemctl restart dwh-llm   (loading takes up to 15 minutes)\n"
            f"LLM_BIN={args.bin}\n"
            f"LLM_MODEL={model_dir / args.model_file}\n"
            f"LLM_MMPROJ={model_dir / args.mmproj}\n"
            f"LLM_THREADS={args.threads}\n"
            f"LLM_CONTEXT={args.context}\n")


def read_env_text(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def read_env(path: Path) -> dict[str, str]:
    return read_env_text(path.read_text()) if path.is_file() else {}


def env_with(text: str, updates: dict[str, str], header: str = "# I17 AI assistant (tools/server/dwh_ai_setup.py)") -> str:
    """dwh.env with the keys set; other lines (and their order) unchanged."""
    lines, seen = [], set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        lines.append(header)
        lines += [f"{k}={updates[k]}" for k in missing]
    return "\n".join(lines) + "\n"


def run(cmd: list[str], stdin: str | None = None, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, input=stdin, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd="/")
    if check and result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(result.stderr or result.stdout).strip()[-400:]}")
    return result


def plan(args) -> list[tuple[str, str]]:
    """(code, description) of the changes still needed."""
    steps = []
    if not LLM_ENV.is_file() or LLM_ENV.read_text() != llm_env_text(args):
        steps.append(("L1", f"{LLM_ENV}: model {args.model_file}, {args.threads} threads, context {args.context}"))
    if not UNIT_DST.is_file() or UNIT_DST.read_text() != UNIT_SRC.read_text():
        steps.append(("L2", f"{UNIT_DST} from the release, enabled"))
    if not SYSCTL.is_file() or SYSCTL.read_text() != SYSCTL_TEXT:
        steps.append(("L3", f"{SYSCTL}: kernel.numa_balancing = 0"))
    wanted = prep.pg_conf_text(prep.memory_gb(), llm=True)
    if not prep.PG_CONF.is_file() or prep.PG_CONF.read_text() != wanted:
        steps.append(("L4", f"{prep.PG_CONF}: shared_buffers 2GB next to the model, restart PostgreSQL"))
    env = read_env(DWH_ENV)
    if not env.get("DWH_AI_DB_PASSWORD") or args.rotate_password:
        steps.append(("L5", "dwh_ai_ro: new random password -> ALTER ROLE and /etc/dwh/dwh.env (not printed)"))
    elif env.get("DWH_AI_MODEL_LABEL") != args.label or env.get("DWH_AI_LLM_URL") != "http://127.0.0.1:8081":
        steps.append(("L5", f"/etc/dwh/dwh.env: DWH_AI_LLM_URL, DWH_AI_MODEL_LABEL={args.label}"))
    if not has_connect():
        steps.append(("L7", "GRANT CONNECT ON DATABASE dwh TO dwh_ai_ro (in dwh PUBLIC has no CONNECT)"))
    return steps


def has_connect() -> bool:
    """dwh_ai_ro may connect to dwh (in dwh CONNECT is not granted to PUBLIC, only to the DWH roles)."""
    result = run(["runuser", "-u", "postgres", "--", "psql", "-X", "-At", "-d", "dwh", "-c",
                  "SELECT has_database_privilege('dwh_ai_ro', 'dwh', 'CONNECT')"], check=False)
    return result.returncode == 0 and result.stdout.strip() == "t"


def apply(args, steps: list[tuple[str, str]]) -> None:
    codes = {code for code, _ in steps}
    if "L1" in codes:
        LLM_ENV.write_text(llm_env_text(args))
        LLM_ENV.chmod(0o644)
    if "L2" in codes:
        shutil.copyfile(UNIT_SRC, UNIT_DST)
        UNIT_DST.chmod(0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "dwh-llm.service"])
    if "L3" in codes:
        SYSCTL.write_text(SYSCTL_TEXT)
        run(["sysctl", "-q", "-p", str(SYSCTL)])
    if "L4" in codes:
        prep.PG_CONF.write_text(prep.pg_conf_text(prep.memory_gb(), llm=True))
        run(["systemctl", "restart", "postgresql@17-main"])
        time.sleep(3)
    if "L5" in codes:
        env_text = DWH_ENV.read_text()
        updates = {"DWH_AI_LLM_URL": "http://127.0.0.1:8081", "DWH_AI_MODEL_LABEL": args.label}
        if not read_env(DWH_ENV).get("DWH_AI_DB_PASSWORD") or args.rotate_password:
            password = secrets.token_urlsafe(32)
            # The password goes through standard input only: it is not in the process list or in a file.
            run(["runuser", "-u", "postgres", "--", "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "dwh", "-f", "-"],
                stdin=f"ALTER ROLE dwh_ai_ro PASSWORD '{password}';\n")
            updates["DWH_AI_DB_PASSWORD"] = password
        stat = DWH_ENV.stat()
        tmp = DWH_ENV.with_suffix(".env.new")
        tmp.write_text(env_with(env_text, updates))
        os.chown(tmp, stat.st_uid, stat.st_gid)
        tmp.chmod(stat.st_mode & 0o777)
        tmp.replace(DWH_ENV)
    if "L7" in codes:
        run(["runuser", "-u", "postgres", "--", "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "dwh", "-c",
             "GRANT CONNECT ON DATABASE dwh TO dwh_ai_ro"])
    run(["systemctl", "restart", "dwh-llm.service"] if codes & {"L1", "L2", "L3"}
        else ["systemctl", "start", "dwh-llm.service"])
    if codes & {"L4", "L5"}:
        run(["systemctl", "restart", "dwh-api.service", "dwh-dispatcher.service"])


def check(wait_minutes: int) -> list[str]:
    problems = []
    if run(["systemctl", "is-enabled", "dwh-llm.service"], check=False).stdout.strip() != "enabled":
        problems.append("dwh-llm.service is not enabled")
    ready = False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        try:
            with opener.open(HEALTH, timeout=5) as response:
                ready = response.status == 200
        except OSError:
            ready = False
        if ready or time.monotonic() >= deadline:
            break
        print("waiting for the model to load...", flush=True)
        time.sleep(20)
    print(f"model /health: {'ready' if ready else 'NOT ready'}")
    if not ready:
        problems.append("model does not answer /health")
    env = read_env(DWH_ENV)
    password = env.get("DWH_AI_DB_PASSWORD")
    if not password:
        problems.append("DWH_AI_DB_PASSWORD is not in /etc/dwh/dwh.env")
        return problems
    pg_env = dict(os.environ, PGPASSWORD=password, PGCONNECT_TIMEOUT="5")
    conn = ["psql", "-X", "-At", "-h", env.get("DWH_DB_HOST", "127.0.0.1"), "-p", env.get("DWH_DB_PORT", "5432"),
            "-U", "dwh_ai_ro", "-d", env.get("DWH_DB_NAME", "dwh")]
    good = run(conn + ["-c", "SHOW transaction_read_only", "-c", "SELECT count(*) FROM dm.v_retail_application_current"],
               check=False, env=pg_env)
    lines = good.stdout.split()
    if good.returncode != 0 or not lines or lines[0] != "on":
        problems.append("dwh_ai_ro: login or read-only check failed: " + good.stderr.strip()[-200:])
    else:
        print(f"dwh_ai_ro: read-only on, dm.v_retail_application_current rows {lines[1]}")
    bad = run(conn + ["-c", "SELECT count(*) FROM app.user_account"], check=False, env=pg_env)
    if bad.returncode == 0:
        problems.append("dwh_ai_ro can read app.user_account")
    elif "permission denied for schema app" in bad.stderr:
        print("dwh_ai_ro: app.user_account refused (as expected)")
    else:
        # Only a refusal by the rights on schema app counts; a failed login proves nothing.
        problems.append("dwh_ai_ro: app check inconclusive: " + bad.stderr.strip()[-200:])
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rotate-password", action="store_true")
    parser.add_argument("--bin", default="/opt/dwh-llm/b12345/llama-b12345/llama-server")
    parser.add_argument("--model-dir", default="/var/lib/dwh-llm/models/gemma4-26b-a4b")
    parser.add_argument("--model-file", default="gemma-4-26B-A4B-it-UD-Q4_K_M.gguf")
    parser.add_argument("--mmproj", default="mmproj-F16.gguf")
    parser.add_argument("--label", default="gemma4-26b-a4b")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--context", type=int, default=16384)
    parser.add_argument("--wait", type=int, default=20, help="minutes to wait for the model to load")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("AI_SETUP_RESULT: FAIL (run with sudo)")
        return 1
    for path in (Path(args.bin), Path(args.model_dir) / args.model_file, Path(args.model_dir) / args.mmproj, UNIT_SRC):
        if not path.is_file():
            print(f"AI_SETUP_RESULT: FAIL (not found: {path})")
            return 1
    if run(["systemctl", "is-active", "dwh-llm-probe.service"], check=False).stdout.strip() == "active":
        print("AI_SETUP_RESULT: FAIL (the temporary model dwh-llm-probe is running; stop it: "
              "sudo systemctl kill -s KILL dwh-llm-probe)")
        return 1
    if not args.check:
        steps = plan(args)
        for code, text in steps:
            print(f"{code} {text}")
        if not steps:
            print("nothing to change")
        if not args.apply:
            print("AI_SETUP_RESULT: PLAN (run with --apply to make these changes)")
            return 0
        try:
            apply(args, steps)
        except RuntimeError as exc:
            print(f"AI_SETUP_RESULT: FAIL ({exc})")
            return 1
        print("changes applied")
    problems = check(args.wait)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print(f"AI_SETUP_RESULT: {'PASS' if not problems else 'FAIL'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
