"""Master key administration for the SecretStore keyring (I02-05).

Used only by the operator tools (tools/secret_key.ps1 on the Windows PC,
tools/secret_key_tool.py --provider systemd-creds on the Linux server), never by the API:
  new      generate a key, save <key_id>.dpapi (or <key_id>.cred) and an encrypted backup file
  restore  rebuild the key file from a backup file
  verify   check that the key file opens and (optionally) matches a backup

Key protection: Windows DPAPI (CurrentUser) on the PC; on the Linux server
systemd-creds with the host key / TPM2 (I16, decision C9), the credential name
dwh-key.<key_id> is bound into the encrypted file. The backup format is the same
for both, so a key moves between them through its backup file.

The backup is AES-256-GCM encrypted with a key derived from a backup
passphrase by scrypt; the passphrase is typed by the operator and kept
nowhere on the computer (K05). Output shows only key ids and a non-secret
fingerprint (K08, K09).
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from dwh_api.secret_keyring import (
    CREDENTIAL_FILE_SUFFIX, KEY_FILE_SUFFIX, credential_name, key_file, valid_key_id,
)

BACKUP_FORMAT = "DWH-SECRETSTORE-KEY-BACKUP-1"
BACKUP_SUFFIX = ".dwhkey"
MIN_PASSPHRASE = 12
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
_FINGERPRINT_LABEL = b"DWH SecretStore key fingerprint v1"


class KeyToolError(Exception):
    """Sanitized failure for the operator."""


def fingerprint(raw: bytes) -> str:
    """Non-secret identifier of a key; cannot be used to recover it."""
    return hmac.new(raw, _FINGERPRINT_LABEL, hashlib.sha256).hexdigest()[:16].upper()


def _kdf(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(passphrase.encode("utf-8"))


def _aad(key_id: str, print_: str) -> bytes:
    return f"{BACKUP_FORMAT}|{key_id}|{print_}".encode("ascii")


def make_backup(raw: bytes, key_id: str, passphrase: str) -> bytes:
    if len(raw) != 32 or not valid_key_id(key_id):
        raise KeyToolError("Invalid key")
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        raise KeyToolError(f"Backup passphrase must have at least {MIN_PASSPHRASE} characters")
    salt, nonce = os.urandom(16), os.urandom(12)
    print_ = fingerprint(raw)
    ciphertext = AESGCM(_kdf(passphrase, salt)).encrypt(nonce, raw, _aad(key_id, print_))
    document = {
        "format": BACKUP_FORMAT, "key_id": key_id, "fingerprint": print_,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": base64.b64encode(salt).decode()},
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    return (json.dumps(document, indent=1) + "\n").encode("ascii")


def backup_header(data: bytes) -> tuple[str, str]:
    try:
        document = json.loads(data.decode("ascii"))
        if document["format"] != BACKUP_FORMAT or not valid_key_id(document["key_id"]):
            raise ValueError()
        return document["key_id"], document["fingerprint"]
    except Exception:
        raise KeyToolError("Not a DWH key backup file") from None


def open_backup(data: bytes, passphrase: str) -> tuple[str, bytes]:
    key_id, print_ = backup_header(data)
    try:
        document = json.loads(data.decode("ascii"))
        kdf = document["kdf"]
        if kdf["name"] != "scrypt":
            raise ValueError()
        derived = Scrypt(salt=base64.b64decode(kdf["salt"]), length=32,
                         n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"])).derive(passphrase.encode("utf-8"))
        raw = AESGCM(derived).decrypt(base64.b64decode(document["nonce"]),
                                      base64.b64decode(document["ciphertext"]), _aad(key_id, print_))
    except InvalidTag:
        raise KeyToolError("Wrong passphrase or damaged backup") from None
    except Exception:
        raise KeyToolError("Not a DWH key backup file") from None
    if len(raw) != 32 or not hmac.compare_digest(fingerprint(raw), print_):
        raise KeyToolError("Wrong passphrase or damaged backup")
    return key_id, raw


# ----- Windows DPAPI (CurrentUser) -----------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise KeyToolError("Windows DPAPI is not available here")
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    try:
        # CRYPTPROTECT_UI_FORBIDDEN; CurrentUser scope; no extra entropy.
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
        if not ok or not result.pbData:
            raise KeyToolError("Windows DPAPI refused the operation")
        return bytes(ctypes.string_at(result.pbData, result.cbData))
    finally:
        if result.pbData:
            ctypes.memset(result.pbData, 0, result.cbData)
            ctypes.windll.kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
        ctypes.memset(buffer, 0, len(data))


def dpapi_protect(raw: bytes) -> bytes:
    return _dpapi(raw, True)


def dpapi_unprotect(blob: bytes) -> bytes:
    return _dpapi(blob, False)


# ----- Linux: systemd-creds (I16, decision C9) -------------------------------

SYSTEMD_CREDS = "/usr/bin/systemd-creds"


def _systemd_creds(action: str, data: bytes, key_id: str, run=None) -> bytes:
    run = run or subprocess.run
    if os.name == "nt":
        raise KeyToolError("systemd-creds is not available here")
    try:
        name = credential_name(key_id)
    except Exception:
        raise KeyToolError("Invalid key id") from None
    try:
        # --with-key=auto (default): host key, plus TPM2 when the server has one.
        result = run([SYSTEMD_CREDS, action, f"--name={name}", "-", "-"], input=data,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise KeyToolError("systemd-creds could not be started") from None
    if result.returncode != 0 or not result.stdout:
        # stderr may name files; it never carries the key, but it is not shown anyway.
        raise KeyToolError("systemd-creds refused the operation")
    return result.stdout


def systemd_protect(raw: bytes, key_id: str) -> bytes:
    return _systemd_creds("encrypt", raw, key_id)


def systemd_unprotect(blob: bytes, key_id: str) -> bytes:
    return _systemd_creds("decrypt", blob, key_id)


@dataclass(frozen=True)
class Protector:
    """How key files are protected: file suffix, protect(raw, key_id), unprotect(blob, key_id)."""
    suffix: str
    protect: Callable[[bytes, str], bytes]
    unprotect: Callable[[bytes, str], bytes]


SYSTEMD_PROTECTOR = Protector(CREDENTIAL_FILE_SUFFIX, systemd_protect, systemd_unprotect)


def _dpapi_protector(protect, unprotect) -> Protector:
    return Protector(KEY_FILE_SUFFIX, lambda raw, key_id: protect(raw), lambda blob, key_id: unprotect(blob))


# ----- Operations -----------------------------------------------------------

def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def create_key(keyring: Path, backup_dir: Path, key_id: str, passphrase: str,
               protect=dpapi_protect, unprotect=dpapi_unprotect, protector: Protector | None = None) -> tuple[str, Path]:
    p = protector or _dpapi_protector(protect, unprotect)
    if not valid_key_id(key_id):
        raise KeyToolError("Invalid key id")
    if not keyring.is_dir() or not backup_dir.is_dir():
        raise KeyToolError("Keyring or backup folder does not exist")
    if keyring.resolve() == backup_dir.resolve():
        raise KeyToolError("Backup folder must differ from the keyring folder")
    path = key_file(keyring, key_id, p.suffix)
    backup = backup_dir / (key_id + BACKUP_SUFFIX)
    if path.exists() or backup.exists():
        raise KeyToolError("Key or backup with this id already exists")
    raw = secrets.token_bytes(32)
    document = make_backup(raw, key_id, passphrase)
    blob = p.protect(raw, key_id)
    if p.unprotect(blob, key_id) != raw or open_backup(document, passphrase)[1] != raw:
        raise KeyToolError("Self-check failed; nothing was written")
    _write_new(backup, document)
    _write_new(path, blob)
    return fingerprint(raw), backup


def restore_key(keyring: Path, backup_file: Path, passphrase: str,
                protect=dpapi_protect, unprotect=dpapi_unprotect,
                protector: Protector | None = None) -> tuple[str, str, bool]:
    """Returns (key_id, fingerprint, written). Existing matching key is kept."""
    p = protector or _dpapi_protector(protect, unprotect)
    key_id, raw = open_backup(backup_file.read_bytes(), passphrase)
    path = key_file(keyring, key_id, p.suffix)
    if path.exists():
        if fingerprint(p.unprotect(path.read_bytes(), key_id)) != fingerprint(raw):
            raise KeyToolError("A different key with this id is already in the keyring")
        return key_id, fingerprint(raw), False
    blob = p.protect(raw, key_id)
    if p.unprotect(blob, key_id) != raw:
        raise KeyToolError("Self-check failed; nothing was written")
    _write_new(path, blob)
    return key_id, fingerprint(raw), True


def verify_key(keyring: Path, key_id: str, unprotect=dpapi_unprotect, protector: Protector | None = None) -> str:
    p = protector or _dpapi_protector(dpapi_protect, unprotect)
    path = key_file(keyring, key_id, p.suffix)
    try:
        raw = p.unprotect(path.read_bytes(), key_id)
    except KeyToolError:
        raise
    except Exception:
        raise KeyToolError("Key file missing or unreadable") from None
    if len(raw) != 32:
        raise KeyToolError("Key file is damaged")
    return fingerprint(raw)


# ----- Linux activation: settings and service drop-in ---------------------------

SYSTEMD_ENV_FILE = Path("/etc/dwh/keyring.env")
SYSTEMD_DROPIN = Path("/etc/systemd/system/dwh-.service.d/10-keyring.conf")


def keyring_key_ids(keyring: Path, suffix: str = CREDENTIAL_FILE_SUFFIX) -> list[str]:
    ids = sorted(item.name[:-len(suffix)] for item in keyring.iterdir()
                 if item.is_file() and not item.is_symlink() and item.name.endswith(suffix))
    return [key_id for key_id in ids if valid_key_id(key_id)]


def systemd_activation_texts(keyring: Path, active_key_id: str, key_ids: list[str],
                             env_file: Path = SYSTEMD_ENV_FILE) -> tuple[str, str]:
    """(settings file, drop-in for every dwh-*.service): the services load every key of the
    keyring as an encrypted credential; the active one encrypts new credentials."""
    if not valid_key_id(active_key_id) or active_key_id not in key_ids:
        raise KeyToolError("Active key is not in the keyring")
    env_text = ("# I16 SecretStore keys: systemd credentials, written by secret_key_tool.py activate\n"
                "DWH_SECRET_KEY_PROVIDER=systemd-creds\n"
                f"DWH_SECRET_ACTIVE_KEY_ID={active_key_id}\n")
    lines = ["# I16 SecretStore keys for every dwh-*.service; written by secret_key_tool.py activate",
             "[Service]", f"EnvironmentFile={env_file}"]
    lines += [f"LoadCredentialEncrypted={credential_name(key_id)}:{keyring / (key_id + CREDENTIAL_FILE_SUFFIX)}"
              for key_id in key_ids]
    return env_text, "\n".join(lines) + "\n"


def write_atomic(path: Path, text: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ----- .env settings --------------------------------------------------------

OBSOLETE_ENV = ("DWH_CONNECTOR_MASTER_KEY_B64", "DWH_CONNECTOR_MASTER_KEY_ID")
_OBSOLETE_COMMENT = "# I03 source credentials"


def update_env_text(text: str, settings: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Set settings and drop the old plain-text key lines (decision D4).

    Other lines are kept byte for byte. Returns (text, set names, removed names).
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    removed: list[str] = []
    pending = dict(settings)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        name = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if name in OBSOLETE_ENV:
            removed.append(name)
            continue
        if stripped.startswith(_OBSOLETE_COMMENT):
            continue
        if name in pending:
            out.append(f"{name}={pending.pop(name)}")
            continue
        out.append(line)
    if pending:
        out.append("# I02-05 SecretStore keyring (master keys are DPAPI files, not here)")
        out.extend(f"{name}={value}" for name, value in pending.items())
    return newline.join(out) + newline, sorted(settings), removed


_SECRET_NAME = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "KEY_B64", "PRIVATE_KEY", "API_KEY")


def mask_env_text(text: str) -> str:
    """Copy of .env for the local backup: obsolete key lines and every value whose
    name looks secret (PASSWORD, SECRET, TOKEN, ...) are replaced by <removed>."""
    result = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        name = line.split("=", 1)[0].strip() if "=" in line and not stripped.startswith("#") else ""
        secret = name in OBSOLETE_ENV or any(part in name.upper() for part in _SECRET_NAME)
        ending = "\r\n" if line.endswith("\r\n") else "\n"
        result.append(f"{name}=<removed>{ending}" if name and secret else line)
    return "".join(result)

