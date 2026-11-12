"""Read-only check of the prepared DWH server against the checklist (plan I16, section 4; step 4).

  sudo python3 dwh_check_server.py

Changes nothing. Prints PASS / WARN / FAIL per item and SERVER_RESULT: PASS / FAIL
(WARN does not fail: things that come later, e.g. the certificate or the dwh_* folders).
Uses the system python3 only.
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

RESULTS: list[tuple[str, str]] = []


def add(level: str, text: str) -> None:
    RESULTS.append((level, text))


def out(cmd: list[str]) -> str:
    """Standard output of a command, or "" when it fails or is missing."""
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def first(text: str) -> str:
    return text.splitlines()[0] if text else ""


def os_release() -> dict:
    data = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key] = value.strip('"')
    except OSError:
        pass
    return data


def mount_of(path: str) -> tuple[str, str] | None:
    """(device, mount point) of the file system that holds path."""
    found = None
    try:
        for line in Path("/proc/self/mounts").read_text().splitlines():
            device, point = line.split()[:2]
            if path == point or path.startswith(point.rstrip("/") + "/"):
                if found is None or len(point) > len(found[1]):
                    found = (device, point)
    except OSError:
        return None
    return found


def rotational(device: str) -> str:
    """'SSD', 'HDD' or 'unknown' for a block device (LVM and partitions resolved through sysfs)."""
    try:
        real = Path(os.path.realpath(device)).name
        base = Path("/sys/class/block") / real
        candidates = [base]
        slaves = base / "slaves"
        if slaves.is_dir():
            candidates = [Path("/sys/class/block") / s.name for s in slaves.iterdir()]
        values = set()
        for cand in candidates:
            queue = cand / "queue" / "rotational"
            if not queue.exists() and (cand / "partition").exists():
                queue = cand.resolve().parent / "queue" / "rotational"
            if queue.exists():
                values.add(queue.read_text().strip())
        if values == {"0"}:
            return "SSD"
        if values == {"1"}:
            return "HDD"
    except OSError:
        pass
    return "unknown"


def check_system() -> None:
    rel = os_release()
    name = f"{rel.get('PRETTY_NAME', 'unknown')}"
    add("PASS" if rel.get("ID") == "debian" and rel.get("VERSION_ID") == "13" else "FAIL", f"OS: {name} (needed Debian 13)")
    cpus = os.cpu_count() or 0
    add("PASS" if cpus >= 16 else "WARN", f"CPU cores: {cpus} (planned 16)")
    try:
        mem_kb = int(next(l for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")).split()[1])
    except (OSError, StopIteration, ValueError):
        mem_kb = 0
    add("PASS" if mem_kb >= 30 * 1024 * 1024 else "WARN", f"memory: {mem_kb // 1024 // 1024} GB (planned 32)")
    tz = out(["timedatectl", "show", "-p", "Timezone", "--value"])
    add("PASS" if tz == "Asia/Baku" else "FAIL", f"time zone: {tz or 'unknown'} (needed Asia/Baku)")
    ntp = out(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    add("PASS" if ntp == "yes" else "FAIL", f"time synchronised: {ntp or 'unknown'}")
    locales = out(["locale", "-a"]).lower()
    add("PASS" if "en_us.utf8" in locales else "WARN", "locale en_US.UTF-8 " + ("present" if "en_us.utf8" in locales else "missing"))
    add("PASS" if not Path("/usr/share/xsessions").exists() else "WARN", "no graphical desktop")


def check_disks() -> None:
    """Layout agreed on 12.11.2026 (plan I16, 11.3): system SSD ~200 GB with /backup on it,
    a separate SSD ~500 GB for /var/lib/postgresql. Virtual disks often report themselves as
    rotational, so "HDD" is a warning to confirm, not a failure."""
    db_mount = mount_of("/var/lib/postgresql")
    for path, size_gb, separate_needed, what in (("/", 150, False, "system"),
                                                 ("/var/lib/postgresql", 400, True, "database"),
                                                 ("/backup", 0, False, "copies")):
        mount = mount_of(path)
        if mount is None or not Path(path).exists():
            add("FAIL", f"disk {what}: {path} missing")
            continue
        total = shutil.disk_usage(path).total // 1024 ** 3
        media = rotational(mount[0])
        level, notes = "PASS", []
        if separate_needed and mount[1] != path:
            level = "FAIL"
            notes.append("not a separate disk")
        if path == "/backup" and db_mount is not None and mount[0] == db_mount[0]:
            level = "FAIL"
            notes.append("on the same disk as the database")
        if total < size_gb:
            level = "FAIL" if level == "FAIL" else "WARN"
            notes.append("smaller than planned")
        if media == "HDD" and path != "/backup":
            level = "FAIL" if level == "FAIL" else "WARN"
            notes.append("reported as HDD - confirm SSD (virtual disks often report HDD)")
        add(level, f"disk {what}: {path} on {mount[0]} ({mount[1]}), {total} GB, {media}"
            + (f" - {', '.join(notes)}" if notes else ""))
    fstab = Path("/etc/fstab").read_text() if Path("/etc/fstab").exists() else ""
    present = " /var/lib/postgresql " in fstab or "\t/var/lib/postgresql\t" in fstab
    add("PASS" if present else "FAIL", "/var/lib/postgresql in /etc/fstab " + ("(survives reboot)" if present else "not found"))


def check_users_dirs() -> None:
    try:
        user = pwd.getpwnam("dwh")
        add("PASS" if user.pw_shell.endswith("nologin") else "WARN",
            f"user dwh: home {user.pw_dir}, shell {user.pw_shell}")
    except KeyError:
        add("FAIL", "user dwh missing")
    incoming = Path("/srv/dwh/incoming")
    add("PASS" if incoming.is_dir() else "FAIL", f"{incoming} " + ("present" if incoming.is_dir() else "missing"))
    sshd = out(["sshd", "-T"]).lower()
    if sshd:
        add("PASS" if "permitrootlogin no" in sshd else "WARN", "SSH: root login " + ("off" if "permitrootlogin no" in sshd else "allowed"))
        add("PASS" if "passwordauthentication no" in sshd else "WARN",
            "SSH: password login " + ("off" if "passwordauthentication no" in sshd else "allowed"))


def check_programs() -> None:
    psql = out(["psql", "--version"])
    add("PASS" if " 17." in psql else "FAIL", f"PostgreSQL client: {psql or 'missing'} (needed 17)")
    clusters = out(["pg_lsclusters", "--no-header"])
    add("PASS" if clusters.startswith("17 ") and "online" in clusters else "FAIL",
        f"PostgreSQL cluster: {first(clusters) or 'none'} (needed 17 main online)")
    listen = out(["runuser", "-u", "postgres", "--", "psql", "-X", "-At", "-c", "SHOW listen_addresses"])
    add("PASS" if listen in ("localhost", "127.0.0.1", "localhost,127.0.0.1") else "FAIL",
        f"PostgreSQL listen_addresses: {listen or 'unknown'} (needed localhost)")
    for name, needed in (("shared_buffers", "8GB"), ("timezone", "Asia/Baku")):
        value = out(["runuser", "-u", "postgres", "--", "psql", "-X", "-At", "-c", f"SHOW {name}"])
        add("PASS" if value == needed else "WARN", f"PostgreSQL {name}: {value or 'unknown'} (planned {needed})")
    has_dwh = out(["runuser", "-u", "postgres", "--", "psql", "-X", "-At", "-c",
                   "SELECT count(*) FROM pg_database WHERE datname = 'dwh'"])
    state = {"0": "not created yet (correct: it comes from the copy)", "1": "already exists"}.get(has_dwh, "unknown")
    add("PASS" if has_dwh == "0" else "WARN", f"database dwh: {state}")
    py = out(["python3", "--version"])
    add("PASS" if py.startswith("Python 3.13") else "FAIL", f"{py or 'python3 missing'} (needed 3.13)")
    venv = subprocess.run(["python3", "-c", "import venv, ensurepip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    add("PASS" if venv == 0 else "FAIL", "python3-venv " + ("present" if venv == 0 else "missing"))
    for program in ("nginx", "git", "unzip", "systemd-creds", "sha256sum"):
        found = shutil.which(program) or (f"/usr/sbin/{program}" if Path(f"/usr/sbin/{program}").exists() else None)
        add("PASS" if found else "FAIL", f"{program}: {'present' if found else 'missing'}")
    tpm = Path("/dev/tpmrm0").exists() or Path("/dev/tpm0").exists()
    add("PASS" if tpm else "WARN", "TPM 2.0: " + ("present (keys bound to it)" if tpm else "none (keys bound to the host key only)"))
    for program in ("node", "npm", "gcc"):
        add("PASS" if not shutil.which(program) else "WARN", f"{program}: " + ("absent (not needed)" if not shutil.which(program) else "installed, not needed"))


def check_network_side() -> None:
    ufw = out(["ufw", "status"])
    add("PASS" if ufw.startswith("Status: active") else "WARN", "firewall ufw: " + (ufw.splitlines()[0] if ufw else "not installed"))
    listening = out(["ss", "-Hltn"])
    exposed = sorted({line.split()[3] for line in listening.splitlines()
                      if len(line.split()) > 3 and not line.split()[3].startswith(("127.", "[::1]"))})
    add("PASS" if all(a.endswith((":22", ":443")) for a in exposed) else "WARN",
        "ports open to the network: " + (", ".join(exposed) or "none") + " (planned 22, 443)")
    cert = Path("/etc/dwh/tls/dwh.crt")
    add("PASS" if cert.is_file() else "WARN", f"certificate {cert}: " + ("present" if cert.is_file() else "not yet (needed by step 9)"))


def main() -> int:
    if os.name != "posix" or os.geteuid() != 0:
        print("SERVER_RESULT: FAIL (run with sudo)")
        return 1
    for check in (check_system, check_disks, check_users_dirs, check_programs, check_network_side):
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - a broken check is reported, the others still run
            add("FAIL", f"{check.__name__}: {type(exc).__name__}: {exc}")
    for level, text in RESULTS:
        print(f"{level:4}  {text}")
    failed = sum(1 for level, _ in RESULTS if level == "FAIL")
    warned = sum(1 for level, _ in RESULTS if level == "WARN")
    print(f"SERVER_RESULT: {'PASS' if not failed else 'FAIL'} ({len(RESULTS) - failed - warned} PASS, {warned} WARN, {failed} FAIL)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
