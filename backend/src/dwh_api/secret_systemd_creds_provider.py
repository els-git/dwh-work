"""Linux master-key loader for SecretStore: systemd credentials (I16, decision C9).

On the Linux server every master key is kept as <key_id>.cred, encrypted by
`systemd-creds encrypt --name=dwh-key.<key_id>` with the host key (and TPM2
when present). The DWH services load the keys with LoadCredentialEncrypted=;
systemd decrypts them into $CREDENTIALS_DIRECTORY (memory, readable only by
the service). This loader reads one such decrypted 32-byte key and fails closed
on everything else. No key is printed, returned or written anywhere.
"""
from __future__ import annotations

from pathlib import Path

from dwh_api.secret_store import SecretStore


class SystemdCredentialUnavailable(Exception):
    pass


def load_crypto_from_credential_file(path: Path) -> SecretStore:
    """Crypto core from a decrypted credential file; fail closed on any problem."""
    try:
        if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError()
        if path.stat().st_size != 32:
            raise ValueError()
        raw = path.read_bytes()
        try:
            if len(raw) != 32:
                raise ValueError()
            return SecretStore(raw)
        finally:
            del raw
    except Exception:
        raise SystemdCredentialUnavailable("Master key unavailable") from None
