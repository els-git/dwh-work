"""Install a DWH release package on the server (I16, decision C4). Run as root.

  sudo python3 dwh_update.py /srv/dwh/incoming/dwh_release_<version>.zip [--first-install]
  sudo python3 dwh_update.py --rollback-to <version>     back to an installed release (manual rollback)

Steps (any failure before the switch leaves the running release untouched):
  1. SHA256 of the package against dwh_release_<version>.sha256 next to it;
  2. safe unpacking into /opt/dwh/releases/.staging-<version>; SHA256 of every file against
     manifest.json, no extra or missing files;
  3. code from repo.bundle at the commit of the manifest; the built frontend;
     data/ and runtime/ of the code are links to /var/lib/dwh (kept between releases);
  4. Python environment from the wheels of the package, offline (pip --no-index);
  5. the release becomes /opt/dwh/releases/<version>;
  6. copy of the database (dwh_backup.py as postgres, label pre-update-<version>), unless --first-install;
  7. systemd units of the release installed when changed; services stopped (dispatcher first: its
     current iteration finishes), /opt/dwh/current switched, services started;
  8. check: /api/health answers 200 and both services are active (up to 90 s);
     otherwise /opt/dwh/current goes back to the previous release and it is started again.
The database is not changed by an update (SQL packages are applied separately with APPLY).
Prints UPDATE_RESULT: PASS / FAIL / ROLLED_BACK (INSTALLED: first installation whose services are not
healthy yet - expected before the database and keys are moved). Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

FORMAT = "DWH-RELEASE-1"
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,39}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
UNITS = ("dwh-api.service", "dwh-dispatcher.service", "dwh-backup.service", "dwh-backup.timer")
SERVICES_STOP_ORDER = ("dwh-dispatcher.service", "dwh-api.service")
SERVICES_START_ORDER = ("dwh-api.service", "dwh-dispatcher.service")


class UpdateError(Exception):
    pass


@dataclass
class Settings:
    root: Path = Path("/opt/dwh")
    state: Path = Path("/var/lib/dwh")
    unit_dir: Path = Path("/etc/systemd/system")
    backup_dir: Path = Path("/backup/dwh")
    python: str = "/usr/bin/python3"
    health_url: str = "http://127.0.0.1:8000/api/health"
    health_seconds: int = 90
    keep: int = 3
    service_user: str = "dwh"
    run: object = field(default=None)  # command runner (tests)
    log: object = field(default=None)

    def __post_init__(self):
        self.run = self.run or run_command
        self.log = self.log or (lambda message: print(message, flush=True))

    @property
    def releases(self) -> Path:
        return self.root / "releases"

    @property
    def current(self) -> Path:
        return self.root / "current"


def run_command(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kwargs)


def must(settings: Settings, cmd: list[str], what: str, **kwargs) -> str:
    result = settings.run(cmd, **kwargs)
    if result.returncode != 0:
        tail = (result.stdout or "").strip().splitlines()[-5:]
        raise UpdateError(f"{what} failed (exit code {result.returncode})" + (": " + " | ".join(tail) if tail else ""))
    return result.stdout or ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ----- 1-2: package checks and unpacking ---------------------------------------------

def check_package_checksum(package: Path) -> None:
    checksum = package.with_suffix(".sha256")
    if not checksum.is_file():
        raise UpdateError(f"{checksum.name} not found next to the package")
    parts = checksum.read_text(encoding="ascii").split()
    if len(parts) != 2 or parts[1].lstrip("*") != package.name or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
        raise UpdateError(f"{checksum.name} is not a sha256sum line for {package.name}")
    if sha256_file(package).lower() != parts[0].lower():
        raise UpdateError("SHA256 of the package does not match")


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name


def unpack(package: Path, staging: Path) -> dict:
    with zipfile.ZipFile(package) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if not all(safe_member(name) for name in names):
            raise UpdateError("Package contains an unsafe path")
        for info in archive.infolist():
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise UpdateError("Package contains a symbolic link")
        archive.extractall(staging)
    try:
        manifest = json.loads((staging / "manifest.json").read_text(encoding="ascii"))
    except (OSError, ValueError):
        raise UpdateError("manifest.json missing or damaged") from None
    if manifest.get("format") != FORMAT or not VERSION_RE.match(str(manifest.get("version", ""))) \
            or not COMMIT_RE.match(str(manifest.get("commit", ""))):
        raise UpdateError("manifest.json: unknown format, version or commit")
    files = manifest.get("files") or {}
    present = {name for name in names if name != "manifest.json"}
    if set(files) != present:
        raise UpdateError(f"Files differ from the manifest: missing {sorted(set(files) - present)[:5]}, "
                          f"extra {sorted(present - set(files))[:5]}")
    for name, digest in files.items():
        if sha256_file(staging / name) != digest:
            raise UpdateError(f"SHA256 does not match for {name}")
    return manifest


# ----- 3-5: release folder ---------------------------------------------------------

def prepare_release(settings: Settings, staging: Path, manifest: dict) -> Path:
    commit = manifest["commit"]
    src = staging / "src"
    must(settings, ["git", "clone", "--quiet", "--no-checkout", str(staging / "repo.bundle"), str(src)], "git clone")
    must(settings, ["git", "-C", str(src), "-c", "advice.detachedHead=false", "checkout", "--quiet", commit],
         "git checkout")
    head = must(settings, ["git", "-C", str(src), "rev-parse", "HEAD"], "git rev-parse").strip()
    if head != commit:
        raise UpdateError("Checked out commit differs from the manifest")
    shutil.rmtree(src / ".git")
    dist = src / "frontend" / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    shutil.copytree(staging / "frontend_dist", dist)
    for name in ("data", "runtime"):
        target = settings.state / name
        target.mkdir(parents=True, exist_ok=True)
        link = src / name
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
        link.symlink_to(target)
    venv = staging / "venv"
    must(settings, [settings.python, "-m", "venv", str(venv)], "python venv")
    must(settings, [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "--no-index",
                    "--disable-pip-version-check", "--find-links", str(staging / "wheels"),
                    "-r", str(src / "deploy" / "requirements-server.txt")], "pip install (offline)")
    must(settings, [str(venv / "bin" / "python"), "-c", "import fastapi, uvicorn, psycopg, cryptography, dotenv"],
         "Python environment check")
    for name in ("wheels", "frontend_dist", "repo.bundle", "dwh_update.py"):
        path = staging / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    target = settings.releases / manifest["version"]
    os.replace(staging, target)
    return target


# ----- 6-8: switch -----------------------------------------------------------------

def current_release(settings: Settings) -> Path | None:
    if settings.current.is_symlink():
        return Path(os.path.realpath(settings.current))
    return None


def switch_to(settings: Settings, release: Path) -> None:
    tmp = settings.root / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(release)
    os.replace(tmp, settings.current)


def install_units(settings: Settings, release: Path) -> bool:
    changed = False
    for unit in UNITS:
        source = release / "src" / "deploy" / "systemd" / unit
        target = settings.unit_dir / unit
        if not source.is_file():
            continue
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
            os.chmod(target, 0o644)
            changed = True
    if changed:
        must(settings, ["systemctl", "daemon-reload"], "systemctl daemon-reload")
    return changed


def healthy(settings: Settings) -> tuple[bool, str]:
    deadline = time.monotonic() + settings.health_seconds
    last = "no answer"
    while True:
        try:
            with urllib.request.urlopen(settings.health_url, timeout=5) as response:  # noqa: S310 - localhost
                if response.status == 200:
                    inactive = [s for s in SERVICES_START_ORDER
                                if settings.run(["systemctl", "is-active", "--quiet", s]).returncode != 0]
                    if not inactive:
                        return True, "health 200, services active"
                    last = f"not active: {', '.join(inactive)}"
                else:
                    last = f"health HTTP {response.status}"
        except OSError as exc:
            last = f"health: {type(exc).__name__}"
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(2)


def restart_on(settings: Settings, release: Path) -> tuple[bool, str]:
    for service in SERVICES_STOP_ORDER:
        settings.run(["systemctl", "stop", service])
    switch_to(settings, release)
    for service in SERVICES_START_ORDER:
        settings.run(["systemctl", "enable", "--now", service])
    settings.run(["systemctl", "enable", "--now", "dwh-backup.timer"])
    return healthy(settings)


def prune(settings: Settings, keep_paths: set[Path]) -> list[Path]:
    releases = sorted((p for p in settings.releases.iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_mtime)
    removed = []
    for old in releases[:-settings.keep] if settings.keep > 0 else []:
        if old.resolve() in keep_paths:
            continue
        shutil.rmtree(old)
        removed.append(old)
    return removed


def update(package: Path, settings: Settings, first_install: bool = False) -> int:
    log = settings.log
    check_package_checksum(package)
    log(f"package SHA256 OK: {package.name}")
    settings.releases.mkdir(parents=True, exist_ok=True)
    version_hint = package.stem.removeprefix("dwh_release_")
    staging = settings.releases / f".staging-{version_hint}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        manifest = unpack(package, staging)
        version = manifest["version"]
        if version != version_hint:
            raise UpdateError("Version in the file name differs from the manifest")
        if (settings.releases / version).exists():
            raise UpdateError(f"Release {version} is already installed")
        log(f"manifest OK: version {version}, commit {manifest['commit']}, {len(manifest['files'])} files")
        release = prepare_release(settings, staging, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log(f"release prepared: {release}")
    previous = current_release(settings)
    try:
        if not first_install:
            if previous is None:
                raise UpdateError("No current release; use --first-install for the first installation")
            must(settings, ["runuser", "-u", "postgres", "--", settings.python,
                            str(previous / "src" / "tools" / "server" / "dwh_backup.py"),
                            "--dir", str(settings.backup_dir), "--keep", "14", "--label", f"pre-update-{version}"],
                 "database copy before the update")
            log("database copy before the update: done")
    except BaseException:
        # Nothing was switched: the prepared release goes away, so the same package can be installed again.
        shutil.rmtree(release, ignore_errors=True)
        raise
    if install_units(settings, release):
        log("systemd units installed or updated")
    ok, detail = restart_on(settings, release)
    if ok:
        removed = prune(settings, {release.resolve()} | ({previous.resolve()} if previous else set()))
        log(f"services: {detail}; old releases removed: {len(removed)}")
        log(f"UPDATE_RESULT: PASS (version {version}, commit {manifest['commit']})")
        return 0
    if previous is None:
        # First installation: database, settings or keys may not be in place yet (plan I16, steps 6-8).
        log(f"services not healthy yet: {detail}")
        log(f"UPDATE_RESULT: INSTALLED (version {version}; services start working once the database, "
            "/etc/dwh/dwh.env and the keys are in place)")
        return 0
    log(f"new release did not start: {detail}")
    install_units(settings, previous)
    back_ok, back_detail = restart_on(settings, previous)
    log(f"previous release {previous.name}: {back_detail}")
    log(f"UPDATE_RESULT: ROLLED_BACK (version {version} not started; {previous.name} "
        f"{'running' if back_ok else 'NOT running - check the services'})")
    return 1


def rollback(version: str, settings: Settings) -> int:
    """Manual rollback: switch /opt/dwh/current to an installed release and check it. The database is not
    touched (copies before every update are in /backup/dwh, labelled pre-update-<version>)."""
    log = settings.log
    if not VERSION_RE.match(version):
        raise UpdateError("Invalid version")
    target = settings.releases / version
    if not (target / "src").is_dir() or not (target / "venv").is_dir():
        raise UpdateError(f"Release {version} is not installed in {settings.releases}")
    previous = current_release(settings)
    if previous is not None and previous == target.resolve():
        raise UpdateError(f"Release {version} is already current")
    if install_units(settings, target):
        log("systemd units of the release installed")
    ok, detail = restart_on(settings, target)
    if ok:
        os.utime(target)  # most recent: kept by the retention of old releases
        log(f"services: {detail}")
        log(f"ROLLBACK_RESULT: PASS (current release {version}"
            + (f", was {previous.name})" if previous else ")"))
        return 0
    log(f"release {version} did not start: {detail}")
    if previous is None:
        log("ROLLBACK_RESULT: FAIL (no previous release to return to)")
        return 1
    install_units(settings, previous)
    back_ok, back_detail = restart_on(settings, previous)
    log(f"previous release {previous.name}: {back_detail}")
    log(f"ROLLBACK_RESULT: FAIL ({version} not started; {previous.name} "
        f"{'running' if back_ok else 'NOT running - check the services'})")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", nargs="?")
    parser.add_argument("--rollback-to", metavar="VERSION", help="switch back to an installed release")
    parser.add_argument("--first-install", action="store_true", help="no current release, no database copy yet")
    parser.add_argument("--root", default="/opt/dwh")
    args = parser.parse_args(argv)
    if os.name != "posix" or os.geteuid() != 0:
        print("UPDATE_RESULT: FAIL (run as root: sudo python3 dwh_update.py ...)")
        return 1
    settings = Settings(root=Path(args.root))
    if bool(args.package) == bool(args.rollback_to):
        print("UPDATE_RESULT: FAIL (give a package or --rollback-to <version>)")
        return 1
    if args.rollback_to:
        try:
            return rollback(args.rollback_to, settings)
        except (UpdateError, OSError) as exc:
            print(f"ROLLBACK_RESULT: FAIL ({exc})")
            return 1
    try:
        return update(Path(args.package).resolve(), settings, args.first_install)
    except (UpdateError, OSError, zipfile.BadZipFile) as exc:
        print(f"UPDATE_RESULT: FAIL ({exc})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
