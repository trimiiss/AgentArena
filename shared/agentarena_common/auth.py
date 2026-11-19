"""Access-token verification and authorization dependencies.

The identity service signs tokens with a private key that never leaves it.
Every other service verifies them with public keys from its JWKS endpoint, so
a compromised service cannot mint tokens for another organization.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from agentarena_common.settings import CommonSettings

logger = logging.getLogger(__name__)


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


ROLE_RANK = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Principal(BaseModel):
    """The authenticated caller. Every query is scoped by ``org_id``."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    org_id: UUID
    role: Role
    email: str
    token: str

    def auth_headers(self) -> dict[str, str]:
        """Headers that forward the caller's identity to another service."""
        return {"Authorization": f"Bearer {self.token}"}


class JwksCache:
    """Caches public keys. An unknown key id triggers a rate-limited refresh."""

    def __init__(self, url: str, *, ttl_seconds: float = 300.0, min_refresh_seconds: float = 5.0) -> None:
        self.url = url
        self.ttl_seconds = ttl_seconds
        self.min_refresh_seconds = min_refresh_seconds
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(self.url)
            response.raise_for_status()
        keys: dict[str, Any] = {}
        for entry in response.json().get("keys", []):
            keys[entry["kid"]] = jwt.PyJWK.from_dict(entry).key
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> Any:
        age = time.monotonic() - self._fetched_at
        stale = age > self.ttl_seconds
        unknown = kid not in self._keys and age > self.min_refresh_seconds
        if stale or unknown:
            try:
                await self._refresh()
            except httpx.HTTPError as exc:
                # Keep serving cached keys when identity is briefly unavailable.
                if kid not in self._keys:
                    raise HTTPException(
                        status.HTTP_503_SERVICE_UNAVAILABLE, detail="Token verification keys unavailable"
                    ) from exc
                logger.warning("jwks refresh failed, using cached keys", extra={"error": str(exc)})
        key = self._keys.get(kid)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unknown signing key")
        return key


class LocalKeys:
    """Public keys held in process, for the service that owns the signing key.

    Fetching them over the network would mean calling itself, which also needs a
    NetworkPolicy path that no other reason justifies.
    """

    def __init__(self, keys: dict[str, Any]) -> None:
        self._keys = keys

    async def get_key(self, kid: str) -> Any:
        key = self._keys.get(kid)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unknown signing key")
        return key


async def verify_token(token: str, settings: CommonSettings, jwks: JwksCache | LocalKeys) -> Principal:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if header.get("alg") != "EdDSA" or "kid" not in header:
        raise unauthorized
    key = await jwks.get_key(header["kid"])
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["EdDSA"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        return Principal(
            user_id=UUID(claims["sub"]),
            org_id=UUID(claims["org"]),
            role=Role(claims["role"]),
            email=claims["email"],
            token=token,
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise unauthorized from exc


async def get_principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    return await verify_token(authorization[7:].strip(), request.app.state.settings, request.app.state.jwks)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_role(minimum: Role) -> Callable[[Principal], Principal]:
    """Dependency factory: the caller's role must be at least ``minimum``."""

    def dependency(principal: CurrentPrincipal) -> Principal:
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Requires role {minimum.value} or higher")
        return principal

    return dependency


CanRead = Annotated[Principal, Depends(require_role(Role.VIEWER))]
CanWrite = Annotated[Principal, Depends(require_role(Role.MEMBER))]
CanAdmin = Annotated[Principal, Depends(require_role(Role.ADMIN))]


class InternalCaller(BaseModel):
    """A trusted service acting for one organization."""

    model_config = ConfigDict(frozen=True)

    org_id: UUID


async def get_internal_caller(
    request: Request,
    x_internal_token: Annotated[str | None, Header()] = None,
    x_org_id: Annotated[UUID | None, Header()] = None,
) -> InternalCaller:
    """Guards /internal/* routes. Lookups stay scoped to the given organization."""
    expected = request.app.state.settings.internal_service_token
    if not expected or not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")
    if x_org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="X-Org-Id header is required")
    return InternalCaller(org_id=x_org_id)


InternalAccess = Annotated[InternalCaller, Depends(get_internal_caller)]
