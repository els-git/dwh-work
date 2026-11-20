"""I13-10: HTTPS certificate of the DWH server from the internal CA (PFX file) and the DNS name.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py install --pfx /etc/dwh/tls/pending/dwh.pfx \
      --name dwh.vtbaze.local --ip 10.1.20.20            check the PFX and show what will change
  ... the same --apply                                    install, nginx -t, reload, check the live server
  sudo python3 .../dwh_tls.py rollback                    put back the previous certificate and nginx file
  sudo python3 .../dwh_tls.py check --name dwh.vtbaze.local [--ip 10.1.20.20]   check the live server only

Plan docs/DWH_I13_10_TLS_CERTIFICATE_PLAN_APPROVED_20261120.md (T1 = B: PFX from the administrators; T3 = the old
address redirects to the DNS name). The PFX password is typed at the prompt and given to openssl on stdin; it is
never printed, stored or put into the environment. Before anything changes: the key matches the certificate, the
certificate is for --name (and --ip), the chain verifies up to a root CA from the PFX (or --ca-file), and it is valid
for more than 30 days. Install: copies of the current files go to /etc/dwh/tls/backup/<time>/, then dwh.crt
(certificate + intermediate CAs), dwh.key (600), ca.crt (root CA, for checks) and the nginx file rendered from
deploy/nginx/dwh.conf with --name. nginx -t and the live check (certificate served for --name, the old address
answers 301 to https://<name>/) failing put the previous files back. Prints TLS_RESULT: PASS / FAIL / PLAN.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import getpass
import hashlib
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent.parent / "deploy" / "nginx" / "dwh.conf"
TEMPLATE_NAME = "dwh.example.local"
TLS_DIR = Path("/etc/dwh/tls")
NGINX_CONF = Path("/etc/nginx/sites-available/dwh.conf")
MIN_DAYS = 30
NAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n?", re.S)


class TlsError(Exception):
    pass


def run(cmd: list[str], stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if check and result.returncode != 0:
        # stderr of openssl / nginx does not contain the password (it is only on stdin)
        raise TlsError(f"{' '.join(cmd[:3])}: {(result.stderr or result.stdout).strip()[:400]}")
    return result


class Openssl:
    def __init__(self, runner=run):
        self.run = runner

    def read_pfx(self, pfx: Path, password: str, work: Path) -> tuple[Path, list[str]]:
        """Key file (PEM, 600) in work and the certificates (PEM texts) of the PFX."""
        key, certs = work / "key.pem", work / "certs.pem"
        last = None
        for extra in ([], ["-legacy"]):           # old PFX files (RC2 / 3DES) need the legacy provider
            try:
                self.run(["openssl", "pkcs12", *extra, "-in", str(pfx), "-passin", "stdin", "-nocerts", "-nodes",
                          "-out", str(key)], stdin=password + "\n")
                self.run(["openssl", "pkcs12", *extra, "-in", str(pfx), "-passin", "stdin", "-nokeys",
                          "-out", str(certs)], stdin=password + "\n")
                key.chmod(0o600)
                return key, PEM_RE.findall(certs.read_text())
            except TlsError as exc:
                last = exc
                if "unsupported" not in str(exc).lower() and "rc2" not in str(exc).lower():
                    break
        raise TlsError(f"PFX not read (wrong password or damaged file): {last}")

    def x509(self, pem_file: Path, *options: str) -> str:
        return self.run(["openssl", "x509", "-in", str(pem_file), "-noout", *options], check=False).stdout.strip()

    def pubkey_of_key(self, key: Path) -> str:
        return self.run(["openssl", "pkey", "-in", str(key), "-pubout"]).stdout.strip()

    def verify(self, ca: Path, untrusted: Path | None, leaf: Path) -> bool:
        cmd = ["openssl", "verify", "-purpose", "sslserver", "-CAfile", str(ca)]
        if untrusted is not None:
            cmd += ["-untrusted", str(untrusted)]
        return self.run(cmd + [str(leaf)], check=False).returncode == 0


def fingerprint(pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()


class Bundle:
    """What will be installed, after every check passed."""

    def __init__(self, key: Path, leaf: str, intermediates: list[str], root: str, facts: dict[str, str]):
        self.key, self.leaf, self.intermediates, self.root, self.facts = key, leaf, intermediates, root, facts

    @property
    def chain_pem(self) -> str:
        return self.leaf + "".join(self.intermediates)


def examine(pfx: Path, password: str, name: str, ip: str | None, ca_file: Path | None, work: Path,
            tool: Openssl) -> tuple[Bundle | None, list[tuple[bool, str]]]:
    checks: list[tuple[bool, str]] = []
    key, certs = tool.read_pfx(pfx, password, work)
    checks.append((bool(certs), f"PFX read: {len(certs)} certificate(s) and a key"))
    pub = tool.pubkey_of_key(key)
    files = []
    for i, pem in enumerate(certs):
        f = work / f"c{i}.pem"
        f.write_text(pem if pem.endswith("\n") else pem + "\n")
        files.append(f)
    leaves = [i for i, f in enumerate(files) if tool.x509(f, "-pubkey") == pub]
    if len(leaves) != 1:
        checks.append((False, "the key of the PFX matches its server certificate"))
        return None, checks
    checks.append((True, "the key of the PFX matches its server certificate"))
    li = leaves[0]
    leaf_file = files[li]
    others = [i for i in range(len(files)) if i != li]
    roots = [i for i in others if tool.x509(files[i], "-subject") .replace("subject=", "")
             == tool.x509(files[i], "-issuer").replace("issuer=", "")]
    inter = [i for i in others if i not in roots]
    facts = {"subject": tool.x509(leaf_file, "-subject"), "issuer": tool.x509(leaf_file, "-issuer"),
             "dates": tool.x509(leaf_file, "-enddate"), "san": tool.x509(leaf_file, "-ext", "subjectAltName"),
             "sha256": fingerprint(certs[li])}
    host_ok = "does match" in tool.x509(leaf_file, "-checkhost", name)
    checks.append((host_ok, f"certificate is for {name}"))
    if ip:
        checks.append(("does match" in tool.x509(leaf_file, "-checkip", ip), f"certificate is for {ip}"))
    valid = tool.run(["openssl", "x509", "-in", str(leaf_file), "-noout", "-checkend", str(MIN_DAYS * 86400)],
                     check=False).returncode == 0
    checks.append((valid, f"certificate valid more than {MIN_DAYS} days ({facts['dates']})"))
    if ca_file is not None:
        root_pem = ca_file.read_text()
    elif roots:
        root_pem = "".join(certs[i] for i in roots)
    else:
        checks.append((False, "root CA: not in the PFX, give --ca-file (Base64 certificate of the CA)"))
        return None, checks
    ca = work / "ca.pem"
    ca.write_text(root_pem)
    untrusted = None
    if inter:
        untrusted = work / "inter.pem"
        untrusted.write_text("".join(certs[i] for i in inter))
    checks.append((tool.verify(ca, untrusted, leaf_file),
                   f"chain verifies up to the root CA ({len(inter)} intermediate certificate(s))"))
    if not all(ok for ok, _ in checks):
        return None, checks
    return Bundle(key, certs[li], [certs[i] for i in inter], root_pem, facts), checks


def render_nginx(template: Path, name: str) -> str:
    text = template.read_text()
    if TEMPLATE_NAME not in text:
        raise TlsError(f"{template}: no {TEMPLATE_NAME} to replace")
    return text.replace(TEMPLATE_NAME, name)


class Paths:
    def __init__(self, tls_dir: Path, nginx_conf: Path):
        self.tls_dir, self.nginx_conf = tls_dir, nginx_conf
        self.crt, self.key, self.ca = tls_dir / "dwh.crt", tls_dir / "dwh.key", tls_dir / "ca.crt"
        self.backups = tls_dir / "backup"

    def current(self) -> list[Path]:
        return [self.crt, self.key, self.ca, self.nginx_conf]


def backup(paths: Paths, now: str) -> Path:
    target = paths.backups / now
    target.mkdir(parents=True, mode=0o700)
    for f in paths.current():
        if f.exists():
            shutil.copy2(f, target / f.name)
    (target / "ABSENT").write_text("".join(f"{f.name}\n" for f in paths.current() if not f.exists()))
    return target


def restore(paths: Paths, source: Path) -> None:
    absent = set((source / "ABSENT").read_text().split()) if (source / "ABSENT").exists() else set()
    for f in paths.current():
        if f.name in absent:
            f.unlink(missing_ok=True)
        elif (source / f.name).exists():
            shutil.copy2(source / f.name, f)


def write(path: Path, text: str, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def live_check(name: str, ip: str | None, ca: Path, want_sha: str | None, host: str = "127.0.0.1",
               port: int = 443, timeout: float = 5.0) -> list[tuple[bool, str]]:
    checks = []
    ctx = ssl.create_default_context(cafile=str(ca))
    try:
        with socket.create_connection((host, port), timeout) as raw, ctx.wrap_socket(raw, server_hostname=name) as s:
            der = s.getpeercert(True)
            s.sendall(f"GET / HTTP/1.1\r\nHost: {name}\r\nConnection: close\r\n\r\n".encode())
            status = s.recv(200).split(b"\r\n", 1)[0].decode(errors="replace")
        served = hashlib.sha256(der).hexdigest().upper()
        checks.append((want_sha is None or served == want_sha,
                       f"https://{name}: trusted certificate {served[:16]}..."))
        checks.append((" 200 " in status + " ", f"https://{name}/ answers {status}"))
    except (OSError, ssl.SSLError) as exc:
        checks.append((False, f"https://{name}: {type(exc).__name__} {exc}"))
    if ip:
        ctx_ip = ssl.create_default_context(cafile=str(ca))
        try:
            # no SNI for an address: nginx answers with its default server, the redirect
            with socket.create_connection((host, port), timeout) as raw, \
                    ctx_ip.wrap_socket(raw, server_hostname=ip) as s:
                s.sendall(f"GET /x?y=1 HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n".encode())
                head = s.recv(2000).decode(errors="replace")
            location = next((line.split(":", 1)[1].strip() for line in head.split("\r\n")
                             if line.lower().startswith("location:")), "")
            checks.append((head.startswith("HTTP/1.1 301") and location == f"https://{name}/x?y=1",
                           f"https://{ip} redirects to {location or '(no redirect)'}"))
        except (OSError, ssl.SSLError) as exc:
            checks.append((False, f"https://{ip}: {type(exc).__name__} {exc}"))
    return checks


def show(checks: list[tuple[bool, str]]) -> bool:
    for ok, text in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {text}")
    return all(ok for ok, _ in checks)


def nginx_reload(runner) -> None:
    runner(["nginx", "-t"])
    runner(["systemctl", "reload", "nginx"])


def install(args, password: str, tool: Openssl, runner=run, live=live_check) -> int:
    paths = Paths(args.tls_dir, args.nginx_conf)
    with tempfile.TemporaryDirectory(prefix="dwh_tls_", dir=args.tls_dir) as tmp:
        work = Path(tmp)
        os.chmod(work, 0o700)
        print(f"PFX {args.pfx}:")
        bundle, checks = examine(args.pfx, password, args.name, args.ip, args.ca_file, work, tool)
        ok = show(checks)
        if bundle is not None:
            for k in ("subject", "issuer", "san", "sha256"):
                print(f"  {k}: {bundle.facts[k]}")
        if not ok or bundle is None:
            print("TLS_RESULT: FAIL (nothing changed)")
            return 1
        conf = render_nginx(args.template, args.name)
        old = paths.nginx_conf.read_text() if paths.nginx_conf.exists() else ""
        print(f"Changes: {paths.crt} (certificate + {len(bundle.intermediates)} intermediate), {paths.key} (600), "
              f"{paths.ca} (root CA); {paths.nginx_conf}:")
        diff = list(difflib.unified_diff(old.splitlines(), conf.splitlines(), str(paths.nginx_conf), "new", lineterm=""))
        print("\n".join("    " + line for line in diff) if diff else "    (no change)")
        if not args.apply:
            print("TLS_RESULT: PLAN (run again with --apply)")
            return 0
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = backup(paths, now)
        print(f"Copy of the current files: {saved}")
        try:
            write(paths.key, bundle.key.read_text(), 0o600)
            write(paths.crt, bundle.chain_pem, 0o644)
            write(paths.ca, bundle.root, 0o644)
            write(paths.nginx_conf, conf, 0o644)
            nginx_reload(runner)
            final = live(args.name, args.ip, paths.ca, bundle.facts["sha256"], port=args.port)
            if not show(final):
                raise TlsError("live check failed")
        except (TlsError, OSError) as exc:
            print(f"  FAIL  {exc}")
            restore(paths, saved)
            try:
                nginx_reload(runner)
                print(f"Previous files put back from {saved}, nginx reloaded")
            except TlsError as again:
                print(f"Previous files put back from {saved}; nginx: {again}")
            print("TLS_RESULT: FAIL (rolled back)")
            return 1
    print(f"TLS_RESULT: PASS (https://{args.name})")
    print(f"The PFX is not needed on the server any more: sudo rm {args.pfx}")
    return 0


def rollback(args, runner=run) -> int:
    paths = Paths(args.tls_dir, args.nginx_conf)
    copies = sorted(p for p in paths.backups.glob("*") if p.is_dir()) if paths.backups.exists() else []
    if not copies:
        print("TLS_RESULT: FAIL (no copy to roll back to)")
        return 1
    restore(paths, copies[-1])
    nginx_reload(runner)
    shutil.rmtree(copies[-1])
    print(f"TLS_RESULT: PASS (rolled back to the files saved {copies[-1].name})")
    return 0


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("action", choices=["install", "rollback", "check"])
    p.add_argument("--pfx", type=Path)
    p.add_argument("--name", help="DNS name users open, e.g. dwh.vtbaze.local")
    p.add_argument("--ip", help="old address, must be in the certificate and redirect to --name")
    p.add_argument("--ca-file", type=Path, help="root CA certificate (only when the PFX has none)")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--port", type=int, default=443)
    p.add_argument("--tls-dir", type=Path, default=TLS_DIR)
    p.add_argument("--nginx-conf", type=Path, default=NGINX_CONF)
    p.add_argument("--template", type=Path, default=TEMPLATE)
    args = p.parse_args(argv)
    if args.action in ("install", "check"):
        if not args.name or not NAME_RE.match(args.name.lower()):
            p.error("give --name: a full DNS name, e.g. dwh.vtbaze.local")
        args.name = args.name.lower()
    if args.ip:
        try:
            socket.inet_aton(args.ip)
        except OSError:
            p.error("--ip is not an IPv4 address")
    if args.action == "install" and (args.pfx is None or not args.pfx.is_file()):
        p.error("give --pfx: the PFX file from the administrators")
    return args


def main(argv=None, password_prompt=getpass.getpass, runner=run, live=live_check) -> int:
    args = parse(argv if argv is not None else sys.argv[1:])
    try:
        if args.action == "rollback":
            return rollback(args, runner)
        if args.action == "check":
            ca = args.tls_dir / "ca.crt"
            ok = show(live(args.name, args.ip, ca, None, port=args.port))
            print(f"TLS_RESULT: {'PASS' if ok else 'FAIL'}")
            return 0 if ok else 1
        password = password_prompt("PFX password (not shown): ")
        return install(args, password, Openssl(runner), runner, live)
    except TlsError as exc:
        print(f"TLS_RESULT: FAIL ({exc})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
