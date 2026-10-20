"""Layer 1 - cryptography. AES-256-GCM with a scrypt-derived key.

Blob layout:  salt(16) | nonce(12) | ciphertext+tag
GCM authenticates the data, so a wrong password or tampered audio is detected
instead of silently returning garbage.
"""
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
OVERHEAD = SALT_LEN + NONCE_LEN + TAG_LEN


class DecryptionError(Exception):
    pass


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return kdf.derive(password.encode("utf-8"))


def encrypt(plaintext: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    key = _derive_key(password, salt)
    return salt + nonce + AESGCM(key).encrypt(nonce, plaintext, b"waveflow-v1")


def decrypt(blob: bytes, password: str) -> bytes:
    if len(blob) < OVERHEAD:
        raise DecryptionError("Payload too short")
    salt, nonce, ct = blob[:SALT_LEN], blob[SALT_LEN:SALT_LEN + NONCE_LEN], blob[SALT_LEN + NONCE_LEN:]
    try:
        return AESGCM(_derive_key(password, salt)).decrypt(nonce, ct, b"waveflow-v1")
    except InvalidTag as exc:
        raise DecryptionError("Wrong password or corrupted audio") from exc


def slot_seed(password: str) -> int:
    """Seed for the key-dependent bin permutation (the 'where' of the hiding)."""
    return int.from_bytes(hashlib.sha256(b"waveflow-slots|" + password.encode("utf-8")).digest()[:8], "big")
