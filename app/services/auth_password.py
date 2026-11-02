"""Hash / vérif mot de passe (scrypt Yayra + bcrypt migré Supabase)."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt_hex = secrets.token_hex(16)
    salt = salt_hex.encode("utf-8")
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return f"{salt_hex}:{derived.hex()}"


def password_needs_upgrade(stored: str | None) -> bool:
    return bool(stored and stored.startswith("bcrypt:"))


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    if stored.startswith("bcrypt:"):
        try:
            import bcrypt
        except ImportError:
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored[7:].encode("utf-8"))
        except ValueError:
            return False

    if ":" not in stored:
        return False
    salt_hex, hash_hex = stored.split(":", 1)
    try:
        salt = salt_hex.encode("utf-8")
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return hmac.compare_digest(derived, expected)
