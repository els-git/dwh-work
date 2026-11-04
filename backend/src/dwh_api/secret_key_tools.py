"""Master key administration for the SecretStore keyring (I02-05).

Used only by tools/secret_key.ps1 on the server, never by the API:
  new      generate a key, save <key_id>.dpapi and an encrypted backup file
  restore  rebuild <key_id>.dpapi from a backup file
  verify   check that the key file opens and (optionally) matches a backup

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
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from dwh_api.secret_keyring import key_file, valid_key_id

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


# ----- Operations -----------------------------------------------------------

def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def create_key(keyring: Path, backup_dir: Path, key_id: str, passphrase: str,
               protect=dpapi_protect, unprotect=dpapi_unprotect) -> tuple[str, Path]:
    if not valid_key_id(key_id):
        raise KeyToolError("Invalid key id")
    if not keyring.is_dir() or not backup_dir.is_dir():
        raise KeyToolError("Keyring or backup folder does not exist")
    if keyring.resolve() == backup_dir.resolve():
        raise KeyToolError("Backup folder must differ from the keyring folder")
    path = key_file(keyring, key_id)
    backup = backup_dir / (key_id + BACKUP_SUFFIX)
    if path.exists() or backup.exists():
        raise KeyToolError("Key or backup with this id already exists")
    raw = secrets.token_bytes(32)
    document = make_backup(raw, key_id, passphrase)
    blob = protect(raw)
    if unprotect(blob) != raw or open_backup(document, passphrase)[1] != raw:
        raise KeyToolError("Self-check failed; nothing was written")
    _write_new(backup, document)
    _write_new(path, blob)
    return fingerprint(raw), backup


def restore_key(keyring: Path, backup_file: Path, passphrase: str,
                protect=dpapi_protect, unprotect=dpapi_unprotect) -> tuple[str, str, bool]:
    """Returns (key_id, fingerprint, written). Existing matching key is kept."""
    key_id, raw = open_backup(backup_file.read_bytes(), passphrase)
    path = key_file(keyring, key_id)
    if path.exists():
        if fingerprint(unprotect(path.read_bytes())) != fingerprint(raw):
            raise KeyToolError("A different key with this id is already in the keyring")
        return key_id, fingerprint(raw), False
    blob = protect(raw)
    if unprotect(blob) != raw:
        raise KeyToolError("Self-check failed; nothing was written")
    _write_new(path, blob)
    return key_id, fingerprint(raw), True


def verify_key(keyring: Path, key_id: str, unprotect=dpapi_unprotect) -> str:
    path = key_file(keyring, key_id)
    try:
        raw = unprotect(path.read_bytes())
    except KeyToolError:
        raise
    except Exception:
        raise KeyToolError("Key file missing or unreadable") from None
    if len(raw) != 32:
        raise KeyToolError("Key file is damaged")
    return fingerprint(raw)


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

