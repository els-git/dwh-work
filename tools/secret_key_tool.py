"""Operator CLI for the SecretStore keyring (I02-05); started by tools/secret_key.ps1 on the PC.

On the Linux server (I16, decision C9) it runs as root with --provider systemd-creds:
  restore / new / verify   key files <key_id>.cred in --keyring (root only, mode 0700)
  activate                 writes /etc/dwh/keyring.env and the drop-in for every dwh-*.service,
                           so the services load all keys as encrypted credentials

Never prints key material or passphrases; shows key ids and fingerprints only.
"""

import argparse
import datetime
import getpass
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
for _path in (PROJECT / "backend" / "packages", PROJECT / "backend" / "src"):
    sys.path.insert(0, str(_path))

from dwh_api import secret_key_tools as kt  # noqa: E402
from dwh_api.secret_keyring import (  # noqa: E402
    ACTIVE_KEY_ID_ENV, KEYRING_DIR_ENV, PROVIDER_DPAPI, PROVIDER_SYSTEMD_CREDS, valid_key_id,
)


def ask_passphrase(confirm: bool) -> str:
    first = getpass.getpass("Backup passphrase (input hidden): ")
    if confirm:
        if len(first) < kt.MIN_PASSPHRASE:
            raise kt.KeyToolError(f"Backup passphrase must have at least {kt.MIN_PASSPHRASE} characters")
        if getpass.getpass("Repeat backup passphrase: ") != first:
            raise kt.KeyToolError("Passphrases differ")
    return first


def activate(keyring: Path, key_id: str) -> None:
    print(f"Key {key_id}: fingerprint {kt.verify_key(keyring, key_id)}")
    env_path = PROJECT / ".env"
    text = env_path.read_bytes().decode("utf-8-sig")  # keep CRLF as is
    backup_dir = PROJECT / "runtime" / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    copy = backup_dir / f"env_before_key_activate_{stamp}.bak"
    copy.write_bytes(kt.mask_env_text(text).encode("utf-8"))
    new_text, names, removed = kt.update_env_text(text, {KEYRING_DIR_ENV: str(keyring), ACTIVE_KEY_ID_ENV: key_id})
    tmp = env_path.with_name(".env.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
    os.replace(tmp, env_path)
    print(f".env: set {', '.join(names)}; removed {', '.join(removed) if removed else 'nothing'}")
    print(f".env copy (old key values removed): {copy}")


def check_linux_keyring(keyring: Path) -> None:
    """The Linux keyring holds encrypted keys only, still: root-owned, nobody else may list or read it."""
    if not keyring.is_absolute() or not keyring.is_dir() or keyring.is_symlink():
        raise kt.KeyToolError("Keyring folder does not exist")
    info = keyring.stat()
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise kt.KeyToolError("Keyring folder must belong to root with mode 0700")


def activate_linux(keyring: Path, key_id: str, env_file: Path, dropin: Path) -> None:
    key_ids = kt.keyring_key_ids(keyring)
    for each in key_ids:
        print(f"Key {each}: fingerprint {kt.verify_key(keyring, each, protector=kt.SYSTEMD_PROTECTOR)}")
    env_text, dropin_text = kt.systemd_activation_texts(keyring, key_id, key_ids, env_file)
    kt.write_atomic(env_file, env_text, 0o640)
    kt.write_atomic(dropin, dropin_text, 0o644)
    print(f"Active key: {key_id}; keys loaded by the services: {', '.join(key_ids)}")
    print(f"Written: {env_file}, {dropin}")
    print("Next: systemctl daemon-reload, then restart the DWH services")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("new", "restore", "verify", "activate"))
    parser.add_argument("--keyring", required=True)
    parser.add_argument("--key-id")
    parser.add_argument("--backup-dir")
    parser.add_argument("--backup-file")
    parser.add_argument("--provider", choices=(PROVIDER_DPAPI, PROVIDER_SYSTEMD_CREDS), default=PROVIDER_DPAPI)
    parser.add_argument("--env-file", default=str(kt.SYSTEMD_ENV_FILE))
    parser.add_argument("--dropin", default=str(kt.SYSTEMD_DROPIN))
    args = parser.parse_args()
    keyring = Path(args.keyring)
    protector = kt.SYSTEMD_PROTECTOR if args.provider == PROVIDER_SYSTEMD_CREDS else None
    try:
        if args.action in ("new", "verify", "activate") and not valid_key_id(args.key_id or ""):
            raise kt.KeyToolError("Invalid key id")
        if protector is not None:
            check_linux_keyring(keyring)
        if args.action == "new":
            print_, backup = kt.create_key(keyring, Path(args.backup_dir or ""), args.key_id, ask_passphrase(True),
                                           protector=protector)
            print(f"Key {args.key_id}: created, fingerprint {print_}")
            print(f"Backup file: {backup}")
            print("Keep the backup file and its passphrase away from this computer and from database backups.")
        elif args.action == "restore":
            key_id, print_, written = kt.restore_key(keyring, Path(args.backup_file or ""), ask_passphrase(False),
                                                     protector=protector)
            print(f"Key {key_id}: {'restored' if written else 'already present, same key'}, fingerprint {print_}")
        elif args.action == "verify":
            print(f"Key {args.key_id}: fingerprint {kt.verify_key(keyring, args.key_id, protector=protector)}")
            if args.backup_file:
                key_id, raw = kt.open_backup(Path(args.backup_file).read_bytes(), ask_passphrase(False))
                if key_id != args.key_id or kt.fingerprint(raw) != kt.verify_key(keyring, args.key_id,
                                                                                  protector=protector):
                    raise kt.KeyToolError("Backup does not match the key")
                print(f"Backup matches key {key_id}")
        elif protector is not None:
            activate_linux(keyring, args.key_id, Path(args.env_file), Path(args.dropin))
        else:
            activate(keyring, args.key_id)
    except (kt.KeyToolError, OSError) as exc:
        if isinstance(exc, kt.KeyToolError):
            message = str(exc)
        else:
            # File errors carry only the error kind and the file path, never contents.
            message = f"File operation failed: {type(exc).__name__}"
            if getattr(exc, "strerror", None):
                message += f", {exc.strerror}"
            if getattr(exc, "filename", None):
                message += f", {exc.filename}"
        print(f"KEY_RESULT: FAIL ({message})")
        return 1
    print("KEY_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
