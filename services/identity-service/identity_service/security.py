"""Password hashing, signing keys and token creation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text

from agentarena_common.db import Database, utcnow
from identity_service.config import Settings
from identity_service.models import SigningKey

_hasher = PasswordHasher()
_KEY_LOCK_ID = 7_236_452

# Each Argon2 operation takes 64 MiB. The thread pool would otherwise run up to 16
# at once, about 1 GiB, and a burst of logins got the pod OOM killed. Waiting here
# bounds memory at 256 MiB, whatever the load.
MAX_CONCURRENT_HASHES = 4
_hash_slots = asyncio.Semaphore(MAX_CONCURRENT_HASHES)
_AAD = b"agentarena-signing-key-v1"

# Verified when the account does not exist, so response time does not reveal
# whether an email is registered.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


async def hash_password(password: str) -> str:
    # Argon2 is deliberately slow. Off the event loop, it cannot stall other requests.
    async with _hash_slots:
        return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored_hash: str | None, password: str) -> bool:
    def verify() -> bool:
        try:
            return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
        except (VerificationError, InvalidHashError):
            return False

    async with _hash_slots:
        return await asyncio.to_thread(verify)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


@dataclass(frozen=True)
class ActiveKey:
    kid: str
    private_key: Ed25519PrivateKey


def _encryption_key(settings: Settings) -> AESGCM:
    key = base64.b64decode(settings.identity_key_encryption_key or "")
    if len(key) != 32:
        raise RuntimeError("IDENTITY_KEY_ENCRYPTION_KEY must be 32 bytes, base64-encoded")
    return AESGCM(key)


def _encrypt(aead: AESGCM, data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + aead.encrypt(nonce, data, _AAD)


def _decrypt(aead: AESGCM, blob: bytes) -> bytes:
    return aead.decrypt(blob[:12], blob[12:], _AAD)


def _public_jwk(kid: str, private_key: Ed25519PrivateKey) -> dict:
    jwk = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(private_key.public_key()))
    return {**jwk, "kid": kid, "use": "sig", "alg": "EdDSA"}


async def load_or_create_signing_key(database: Database, settings: Settings) -> ActiveKey:
    """The advisory lock stops replicas that start together from creating two keys."""
    aead = _encryption_key(settings)
    async with database.sessions() as session, session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _KEY_LOCK_ID})
        row = await session.scalar(
            select(SigningKey).where(SigningKey.retired_at.is_(None)).order_by(SigningKey.created_at.desc()).limit(1)
        )
        if row is None:
            private_key = Ed25519PrivateKey.generate()
            kid = uuid.uuid4().hex
            raw = private_key.private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
            )
            session.add(
                SigningKey(
                    kid=kid, private_key_ciphertext=_encrypt(aead, raw), public_jwk=_public_jwk(kid, private_key)
                )
            )
            return ActiveKey(kid, private_key)
        return ActiveKey(row.kid, Ed25519PrivateKey.from_private_bytes(_decrypt(aead, row.private_key_ciphertext)))


def issue_access_token(
    key: ActiveKey,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str,
    email: str,
) -> str:
    now = utcnow()
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "email": email,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, key.private_key, algorithm="EdDSA", headers={"kid": key.kid})
