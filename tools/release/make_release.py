"""Release package for the DWH server (I16, decision C4). Runs on the PC (tools/make_release.ps1).

  python tools/release/make_release.py --version 2026.11.1 [--out runtime/releases]
                                       [--python-version 3.13] [--skip-build]

dwh_release_<version>.zip contains:
  manifest.json      format, version, commit, Python version, SHA256 of every other file
  repo.bundle        git bundle of the branch main at the released commit
  frontend_dist/     the built frontend (npm run build)
  wheels/            Python packages for Linux (manylinux, x86_64) from deploy/requirements-server.txt
  dwh_update.py      the installer (tools/server/dwh_update.py of the same commit)
Next to it: dwh_release_<version>.sha256 (sha256sum format). No .env, keys or passwords.

Refuses: uncommitted changes in tracked files, a branch other than main, main not equal to origin/main.
Prints RELEASE_RESULT: PASS / FAIL.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
FORMAT = "DWH-RELEASE-1"
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,39}$")
PLATFORMS = ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64")


class ReleaseError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str, cwd: Path = PROJECT) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"git {args[0]} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def check_repository(allow_branch: bool = False) -> str:
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ReleaseError("Uncommitted changes in tracked files; commit or discard them first")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main" and not allow_branch:
        raise ReleaseError(f"Release only from main (current branch: {branch})")
    commit = git("rev-parse", "HEAD")
    if not allow_branch:
        remote = git("rev-parse", "origin/main")
        if remote != commit:
            raise ReleaseError("main differs from origin/main; run git pull first")
    return commit


def build_frontend(skip: bool) -> Path:
    frontend = PROJECT / "frontend"
    if not skip:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        result = subprocess.run([npm, "run", "build"], cwd=frontend)
        if result.returncode != 0:
            raise ReleaseError("Frontend build failed")
    dist = frontend / "dist"
    if not (dist / "index.html").is_file():
        raise ReleaseError("frontend/dist/index.html not found; build the frontend")
    return dist


def download_wheels(target: Path, python_version: str, requirements: Path, wheels_from: Path | None) -> None:
    if wheels_from is not None:
        for wheel in sorted(wheels_from.glob("*.whl")):
            shutil.copy2(wheel, target / wheel.name)
        if not any(target.glob("*.whl")):
            raise ReleaseError(f"No wheels in {wheels_from}")
        return
    short = python_version.replace(".", "")
    cmd = [sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
           "-r", str(requirements), "-d", str(target), "--only-binary=:all:",
           "--implementation", "cp", "--python-version", python_version,
           "--abi", f"cp{short}", "--abi", "abi3", "--abi", "none"]
    for platform in PLATFORMS:
        cmd += ["--platform", platform]
    if subprocess.run(cmd).returncode != 0:
        raise ReleaseError("pip download failed (internet access to PyPI is needed on the PC)")


def build(version: str, out: Path, python_version: str, skip_build: bool, wheels_from: Path | None = None,
          allow_branch: bool = False, now: datetime.datetime | None = None) -> tuple[Path, Path, dict]:
    if not VERSION_RE.match(version):
        raise ReleaseError("Version: letters, digits, '.', '_', '-' (up to 40)")
    commit = check_repository(allow_branch)
    out.mkdir(parents=True, exist_ok=True)
    package = out / f"dwh_release_{version}.zip"
    checksum = out / f"dwh_release_{version}.sha256"
    if package.exists() or checksum.exists():
        raise ReleaseError(f"{package.name} already exists; choose a new version")
    dist = build_frontend(skip_build)
    with tempfile.TemporaryDirectory(prefix="dwh_release_") as tmp:
        stage = Path(tmp)
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        git("bundle", "create", str(stage / "repo.bundle"), branch)
        shutil.copytree(dist, stage / "frontend_dist")
        (stage / "wheels").mkdir()
        download_wheels(stage / "wheels", python_version, PROJECT / "deploy" / "requirements-server.txt",
                        wheels_from)
        shutil.copy2(PROJECT / "tools" / "server" / "dwh_update.py", stage / "dwh_update.py")
        files = {p.relative_to(stage).as_posix(): sha256_file(p)
                 for p in sorted(stage.rglob("*")) if p.is_file()}
        manifest = {"format": FORMAT, "version": version, "commit": commit, "branch": branch,
                    "python": python_version,
                    "created_at": (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds"),
                    "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="ascii")
        partial = package.with_name(package.name + ".partial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ["manifest.json", *files]:
                archive.write(stage / name, name)
        os.replace(partial, package)
    checksum.write_text(f"{sha256_file(package)}  {package.name}\n", encoding="ascii", newline="\n")
    return package, checksum, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", default=str(PROJECT / "runtime" / "releases"))
    parser.add_argument("--python-version", default="3.13")
    parser.add_argument("--skip-build", action="store_true", help="use the existing frontend/dist")
    parser.add_argument("--wheels-from", help="take the wheels from this folder instead of downloading")
    parser.add_argument("--allow-branch", action="store_true", help=argparse.SUPPRESS)  # tests only
    args = parser.parse_args(argv)
    try:
        package, checksum, manifest = build(args.version, Path(args.out), args.python_version, args.skip_build,
                                            Path(args.wheels_from) if args.wheels_from else None, args.allow_branch)
    except (ReleaseError, OSError) as exc:
        print(f"RELEASE_RESULT: FAIL ({exc})")
        return 1
    wheels = sum(1 for name in manifest["files"] if name.startswith("wheels/"))
    print(f"package: {package} ({package.stat().st_size} bytes)")
    print(f"commit: {manifest['commit']}; wheels: {wheels}; Python on the server: {manifest['python']}")
    print(f"SHA256: {checksum.read_text().split()[0].upper()}  ({checksum.name})")
    print("RELEASE_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
