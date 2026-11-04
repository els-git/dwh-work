"""Operator CLI for the SecretStore keyring (I02-05); started by tools/secret_key.ps1.

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
from dwh_api.secret_keyring import ACTIVE_KEY_ID_ENV, KEYRING_DIR_ENV, valid_key_id  # noqa: E402


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("new", "restore", "verify", "activate"))
    parser.add_argument("--keyring", required=True)
    parser.add_argument("--key-id")
    parser.add_argument("--backup-dir")
    parser.add_argument("--backup-file")
    args = parser.parse_args()
    keyring = Path(args.keyring)
    try:
        if args.action in ("new", "verify", "activate") and not valid_key_id(args.key_id or ""):
            raise kt.KeyToolError("Invalid key id")
        if args.action == "new":
            print_, backup = kt.create_key(keyring, Path(args.backup_dir or ""), args.key_id, ask_passphrase(True))
            print(f"Key {args.key_id}: created, fingerprint {print_}")
            print(f"Backup file: {backup}")
            print("Keep the backup file and its passphrase away from this computer and from database backups.")
        elif args.action == "restore":
            key_id, print_, written = kt.restore_key(keyring, Path(args.backup_file or ""), ask_passphrase(False))
            print(f"Key {key_id}: {'restored' if written else 'already present, same key'}, fingerprint {print_}")
        elif args.action == "verify":
            print(f"Key {args.key_id}: fingerprint {kt.verify_key(keyring, args.key_id)}")
            if args.backup_file:
                key_id, raw = kt.open_backup(Path(args.backup_file).read_bytes(), ask_passphrase(False))
                if key_id != args.key_id or kt.fingerprint(raw) != kt.verify_key(keyring, args.key_id):
                    raise kt.KeyToolError("Backup does not match the key")
                print(f"Backup matches key {key_id}")
        else:
            activate(keyring, args.key_id)
    except (kt.KeyToolError, OSError) as exc:
        message = str(exc) if isinstance(exc, kt.KeyToolError) else "File operation failed"
        print(f"KEY_RESULT: FAIL ({message})")
        return 1
    print("KEY_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
