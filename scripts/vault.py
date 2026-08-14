"""Encrypted storage for the Garmin session token.

The repo is public, so the token never touches it in plaintext. It is sealed with
AES (Fernet) under a key derived from the TOKEN_KEY secret via PBKDF2-HMAC-SHA256,
600k iterations. Only the ciphertext is committed.

Why store it in the repo at all: Garmin rotates the refresh token every time it is
used, so the copy in the GitHub secret goes stale after the first run. The sealed
file is the rolling copy; the GARMIN_TOKENS secret is only the bootstrap.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"BJJTOK1\n"
SALT_LEN = 16
ITERATIONS = 600_000


def _fernet(passphrase: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return Fernet(key)


def seal(token_json: str, passphrase: str, path: str | Path) -> None:
    """Encrypt token_json and write it to path (parents created)."""
    salt = os.urandom(SALT_LEN)
    blob = _fernet(passphrase, salt).encrypt(token_json.encode("utf-8"))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(MAGIC + salt + blob)


def unseal(passphrase: str, path: str | Path) -> str | None:
    """Return the decrypted token JSON, or None if the file is absent/unreadable."""
    p = Path(path)
    if not p.exists():
        return None
    raw = p.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError(f"{p} is not a sealed token file")
    salt = raw[len(MAGIC) : len(MAGIC) + SALT_LEN]
    blob = raw[len(MAGIC) + SALT_LEN :]
    try:
        return _fernet(passphrase, salt).decrypt(blob).decode("utf-8")
    except InvalidToken:
        raise ValueError(
            "Could not decrypt the stored token — TOKEN_KEY does not match the one "
            "used to seal data/garmin_token.enc."
        ) from None
