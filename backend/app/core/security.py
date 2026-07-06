"""Credential encryption (Fernet) and secret masking.

The Fernet key derives from credential_secret, falling back to jwt_secret —
both settings must stay even without a login flow, or every stored credential /
model API key silently becomes undecryptable.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    """Build a Fernet from credential_secret, or derive from jwt_secret."""
    raw = settings.credential_secret or settings.jwt_secret
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    if plaintext is None:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def mask_secret(plaintext: str) -> str:
    """Return a masked preview safe to send to clients (never the full value)."""
    if not plaintext:
        return ""
    if len(plaintext) <= 8:
        return "•" * len(plaintext)
    return f"{plaintext[:4]}{'•' * 6}{plaintext[-4:]}"
