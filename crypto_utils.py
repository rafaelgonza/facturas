"""Symmetric encryption of the certificate file at rest.

The .p12 file you upload is encrypted on disk with a key derived
from the SECRET_KEY environment variable (or CERT_ENCRYPTION_KEY if set).
The cert *password* is never stored anywhere - you supply it on each sign.
"""
import os
import base64
import hashlib
from cryptography.fernet import Fernet


def _derive_key() -> bytes:
    """Derive a Fernet-compatible key from CERT_ENCRYPTION_KEY or SECRET_KEY."""
    key_str = os.getenv("CERT_ENCRYPTION_KEY")
    if key_str:
        # If user provided a 32-byte urlsafe-b64 key, use it directly
        try:
            decoded = base64.urlsafe_b64decode(key_str)
            if len(decoded) == 32:
                return key_str.encode()
        except Exception:
            pass
        # Otherwise, hash whatever they gave us to get 32 bytes
        return base64.urlsafe_b64encode(hashlib.sha256(key_str.encode()).digest())

    secret = os.getenv("SECRET_KEY", "default-dev-secret-change-me-in-production")
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    return _fernet().decrypt(data)
