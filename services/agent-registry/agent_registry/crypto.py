"""Authenticated encryption for agent credentials."""

from __future__ import annotations

import base64
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCipher:
    """AES-256-GCM. The organization and agent ids are bound in as associated
    data, so a ciphertext copied onto another row fails to decrypt."""

    def __init__(self, encoded_key: str, key_id: str) -> None:
        key = base64.b64decode(encoded_key or "")
        if len(key) != 32:
            raise RuntimeError("CREDENTIALS_ENCRYPTION_KEY must be 32 bytes, base64-encoded")
        self._aead = AESGCM(key)
        self.key_id = key_id

    @staticmethod
    def _aad(org_id: uuid.UUID, agent_id: uuid.UUID) -> bytes:
        return f"{org_id}:{agent_id}".encode()

    def encrypt(self, plaintext: str, *, org_id: uuid.UUID, agent_id: uuid.UUID) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, plaintext.encode(), self._aad(org_id, agent_id))

    def decrypt(self, blob: bytes, *, org_id: uuid.UUID, agent_id: uuid.UUID) -> str:
        try:
            return self._aead.decrypt(blob[:12], blob[12:], self._aad(org_id, agent_id)).decode()
        except InvalidTag as exc:
            raise ValueError("Credential could not be decrypted") from exc
