"""AES/CBC/PKCS7 cipher."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class SimpleAESCipher:
    """AES/CBC/PKCS7 with IV prepended and Base64-encoded output."""

    def __init__(self, key: str) -> None:
        if key is None or len(key) not in (16, 32):
            raise ValueError("Key length must be 16 or 32 characters")
        self._key_bytes = key.encode("utf-8")

    def encrypt(self, plain_text: str) -> str:
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(self._key_bytes), modes.CBC(iv))
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plain_text.encode("utf-8")) + padder.finalize()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        combined = iv + encrypted
        return base64.b64encode(combined).decode("ascii")

    def decrypt(self, base64_cipher_text: str) -> str:
        combined = base64.b64decode(base64_cipher_text)
        iv = combined[:16]
        ciphertext = combined[16:]
        cipher = Cipher(algorithms.AES(self._key_bytes), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = unpadder.update(padded) + unpadder.finalize()
        return decrypted.decode("utf-8")
