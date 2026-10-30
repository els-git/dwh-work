from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


SCHEME = "scrypt_v1"

SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024

SALT_LENGTH = 16
KEY_LENGTH = 32

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
PASSWORD_REQUIRED_CATEGORIES = 3


def _encode_base64(value: bytes) -> str:
    return (
        base64.urlsafe_b64encode(value)
        .decode("ascii")
        .rstrip("=")
    )


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)

    return base64.urlsafe_b64decode(
        value + padding
    )


def validate_new_password(
    password: str,
    username: str | None = None,
) -> list[str]:
    errors: list[str] = []

    if not isinstance(password, str):
        return [
            "Password must be a string"
        ]

    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(
            (
                "Password must be at least "
                f"{PASSWORD_MIN_LENGTH} characters long"
            )
        )

    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(
            (
                "Password must be no more than "
                f"{PASSWORD_MAX_LENGTH} characters long"
            )
        )

    categories = 0

    if any(char.isupper() for char in password):
        categories += 1

    if any(char.islower() for char in password):
        categories += 1

    if any(char.isdigit() for char in password):
        categories += 1

    if any(
        not char.isalnum()
        and not char.isspace()
        for char in password
    ):
        categories += 1

    if categories < PASSWORD_REQUIRED_CATEGORIES:
        errors.append(
            (
                "Password must include at least 3 of 4 categories: "
                "uppercase letters, lowercase letters, digits, "
                "special characters"
            )
        )

    normalized_username = (
        username.strip().lower()
        if username is not None
        else ""
    )

    if (
        normalized_username
        and normalized_username in password.lower()
    ):
        errors.append(
            "Password must not contain the username"
        )

    return errors


def password_policy_text() -> str:
    return (
        "Password must be 12-128 characters long; "
        "include at least 3 of 4 categories: uppercase letters, "
        "lowercase letters, digits, special characters; "
        "and must not contain the username."
    )


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise TypeError("password must be str")

    if not password:
        raise ValueError("password must not be empty")

    salt = secrets.token_bytes(
        SALT_LENGTH
    )

    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=KEY_LENGTH,
    )

    return "$".join(
        [
            SCHEME,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            _encode_base64(salt),
            _encode_base64(derived_key),
        ]
    )


def verify_password(
    password: str,
    encoded_hash: str | None,
) -> bool:
    if not encoded_hash:
        return False

    try:
        (
            scheme,
            n_value,
            r_value,
            p_value,
            salt_value,
            hash_value,
        ) = encoded_hash.split("$")

        if scheme != SCHEME:
            return False

        salt = _decode_base64(
            salt_value
        )

        expected_hash = _decode_base64(
            hash_value
        )

        actual_hash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            maxmem=SCRYPT_MAXMEM,
            dklen=len(expected_hash),
        )

    except (
        ValueError,
        TypeError,
        base64.binascii.Error,
    ):
        return False

    return hmac.compare_digest(
        actual_hash,
        expected_hash,
    )