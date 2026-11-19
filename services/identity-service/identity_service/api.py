from __future__ import annotations

import re
import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agentarena_common.auth import ROLE_RANK, CanAdmin, CurrentPrincipal, Principal, Role
from agentarena_common.db import get_session, utcnow
from agentarena_common.ratelimit import enforce
from identity_service.config import Settings
from identity_service.models import Membership, Organization, RefreshToken, SigningKey, User
from identity_service.schemas import (
    LoginRequest,
    MemberAdd,
    MemberOut,
    MemberUpdate,
    MeResponse,
    OrganizationCreate,
    OrganizationMembershipOut,
    OrganizationOut,
    OrganizationUpdate,
    RegisterRequest,
    SessionResponse,
    SwitchOrganizationRequest,
    UserOut,
)
from identity_service.security import (
    hash_password,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    verify_password,
)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100] or "org"
    return f"{base}-{secrets.token_hex(3)}"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/v1/auth",
    )


async def _start_session(
    request: Request,
    response: Response,
    session: AsyncSession,
    user: User,
    organization: Organization,
    role: str,
    *,
    family_id: uuid.UUID | None = None,
) -> SessionResponse:
    settings = _settings(request)
    refresh = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            org_id=organization.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_refresh_token(refresh),
            expires_at=utcnow() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    await session.commit()
    _set_refresh_cookie(response, settings, refresh)
    token = issue_access_token(
        request.app.state.signing_key,
        settings,
        user_id=user.id,
        org_id=organization.id,
        role=role,
        email=user.email,
    )
    return SessionResponse(
        access_token=token,
        expires_in=settings.access_token_ttl_seconds,
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(organization),
        role=role,
    )


@router.get("/.well-known/jwks.json", tags=["keys"], summary="Public keys for verifying access tokens")
async def jwks(session: Session) -> dict:
    rows = await session.scalars(select(SigningKey).where(SigningKey.retired_at.is_(None)))
    return {"keys": [row.public_jwk for row in rows]}


@router.post("/v1/auth/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, tags=["auth"])
async def register(body: RegisterRequest, request: Request, response: Response, session: Session) -> SessionResponse:
    settings = _settings(request)
    enforce(request.app.state.register_limiter, _client_ip(request), service=settings.service_name, scope="register")
    user = User(
        email=body.email.lower(),
        password_hash=await hash_password(body.password),
        display_name=body.display_name,
    )
    organization = Organization(name=body.organization_name, slug=_slug(body.organization_name))
    session.add_all([user, organization])
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An account with this email already exists") from exc
    session.add(Membership(user_id=user.id, org_id=organization.id, role=Role.OWNER.value))
    return await _start_session(request, response, session, user, organization, Role.OWNER.value)


@router.post("/v1/auth/login", response_model=SessionResponse, tags=["auth"])
async def login(body: LoginRequest, request: Request, response: Response, session: Session) -> SessionResponse:
    settings = _settings(request)
    email = body.email.lower()
    enforce(
        request.app.state.login_limiter,
        f"{_client_ip(request)}:{email}",
        service=settings.service_name,
        scope="login",
    )
    user = await session.scalar(
        select(User)
        .where(User.email == email)
        .options(selectinload(User.memberships).selectinload(Membership.organization))
    )
    if not await verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This account has no organization")
    membership = next(
        (m for m in user.memberships if m.org_id == body.organization_id),
        None if body.organization_id else min(user.memberships, key=lambda m: m.created_at),
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    user.last_login_at = utcnow()
    return await _start_session(request, response, session, user, membership.organization, membership.role)


@router.post("/v1/auth/refresh", response_model=SessionResponse, tags=["auth"])
async def refresh(
    request: Request,
    response: Response,
    session: Session,
    aa_refresh: Annotated[str | None, Cookie()] = None,
) -> SessionResponse:
    """Rotates the refresh token. Replaying a used token revokes the whole family."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if not aa_refresh:
        raise unauthorized
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(aa_refresh)).with_for_update()
    )
    if token is None:
        raise unauthorized
    now = utcnow()
    if token.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise unauthorized
    if token.expires_at <= now:
        raise unauthorized
    membership = await session.scalar(
        select(Membership)
        .where(Membership.user_id == token.user_id, Membership.org_id == token.org_id)
        .options(selectinload(Membership.user), selectinload(Membership.organization))
    )
    if membership is None:
        raise unauthorized
    token.revoked_at = now
    return await _start_session(
        request,
        response,
        session,
        membership.user,
        membership.organization,
        membership.role,
        family_id=token.family_id,
    )


@router.post("/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def logout(
    request: Request,
    session: Session,
    aa_refresh: Annotated[str | None, Cookie()] = None,
) -> Response:
    settings = _settings(request)
    if aa_refresh:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(aa_refresh), RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.refresh_cookie_name, path="/v1/auth")
    return response


async def _load_membership(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> Membership:
    membership = await session.scalar(
        select(Membership)
        .where(Membership.user_id == user_id, Membership.org_id == org_id)
        .options(selectinload(Membership.user), selectinload(Membership.organization))
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    return membership


@router.post("/v1/auth/switch-organization", response_model=SessionResponse, tags=["auth"])
async def switch_organization(
    body: SwitchOrganizationRequest,
    request: Request,
    response: Response,
    session: Session,
    principal: CurrentPrincipal,
) -> SessionResponse:
    membership = await _load_membership(session, principal.user_id, body.organization_id)
    return await _start_session(request, response, session, membership.user, membership.organization, membership.role)


@router.get("/v1/auth/me", response_model=MeResponse, tags=["auth"])
async def me(session: Session, principal: CurrentPrincipal) -> MeResponse:
    current = await _load_membership(session, principal.user_id, principal.org_id)
    rows = await session.scalars(
        select(Membership)
        .where(Membership.user_id == principal.user_id)
        .options(selectinload(Membership.organization))
        .order_by(Membership.created_at)
    )
    return MeResponse(
        user=UserOut.model_validate(current.user),
        organization=OrganizationOut.model_validate(current.organization),
        role=current.role,
        organizations=[
            OrganizationMembershipOut(organization=OrganizationOut.model_validate(m.organization), role=m.role)
            for m in rows
        ],
    )


@router.post(
    "/v1/organizations", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED, tags=["organizations"]
)
async def create_organization(
    body: OrganizationCreate, session: Session, principal: CurrentPrincipal
) -> OrganizationOut:
    organization = Organization(name=body.name.strip(), slug=_slug(body.name))
    session.add(organization)
    await session.flush()
    session.add(Membership(user_id=principal.user_id, org_id=organization.id, role=Role.OWNER.value))
    await session.commit()
    return OrganizationOut.model_validate(organization)


@router.get("/v1/organizations/current", response_model=OrganizationOut, tags=["organizations"])
async def get_current_organization(session: Session, principal: CurrentPrincipal) -> OrganizationOut:
    organization = await session.get(Organization, principal.org_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return OrganizationOut.model_validate(organization)


@router.patch("/v1/organizations/current", response_model=OrganizationOut, tags=["organizations"])
async def update_current_organization(
    body: OrganizationUpdate, session: Session, principal: CanAdmin
) -> OrganizationOut:
    organization = await session.get(Organization, principal.org_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    organization.name = body.name.strip()
    await session.commit()
    return OrganizationOut.model_validate(organization)


def _member_out(membership: Membership) -> MemberOut:
    return MemberOut(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        role=membership.role,
        joined_at=membership.created_at,
    )


@router.get("/v1/organizations/current/members", response_model=list[MemberOut], tags=["organizations"])
async def list_members(session: Session, principal: CurrentPrincipal) -> list[MemberOut]:
    rows = await session.scalars(
        select(Membership)
        .where(Membership.org_id == principal.org_id)
        .options(selectinload(Membership.user))
        .order_by(Membership.created_at)
    )
    return [_member_out(row) for row in rows]


def _require_can_assign(principal: Principal, role: str) -> None:
    if ROLE_RANK[Role(role)] > ROLE_RANK[principal.role]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot assign a role above your own")


async def _guard_last_owner(session: AsyncSession, membership: Membership) -> None:
    if membership.role != Role.OWNER.value:
        return
    owners = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.org_id == membership.org_id, Membership.role == Role.OWNER.value)
    )
    if (owners or 0) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An organization must keep at least one owner")


@router.post(
    "/v1/organizations/current/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    tags=["organizations"],
)
async def add_member(body: MemberAdd, session: Session, principal: CanAdmin) -> MemberOut:
    _require_can_assign(principal, body.role)
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No account uses this email. Ask them to register first.")
    membership = Membership(user_id=user.id, org_id=principal.org_id, role=body.role)
    session.add(membership)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already a member of this organization") from exc
    await session.refresh(membership, ["user"])
    return _member_out(membership)


async def _target_membership(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    membership = await session.scalar(
        select(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .options(selectinload(Membership.user))
        .with_for_update()
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Member not found")
    return membership


@router.patch("/v1/organizations/current/members/{user_id}", response_model=MemberOut, tags=["organizations"])
async def update_member(user_id: uuid.UUID, body: MemberUpdate, session: Session, principal: CanAdmin) -> MemberOut:
    _require_can_assign(principal, body.role)
    membership = await _target_membership(session, principal.org_id, user_id)
    _require_can_assign(principal, membership.role)
    if body.role != Role.OWNER.value:
        await _guard_last_owner(session, membership)
    membership.role = body.role
    await session.commit()
    return _member_out(membership)


@router.delete(
    "/v1/organizations/current/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["organizations"]
)
async def remove_member(user_id: uuid.UUID, session: Session, principal: CanAdmin) -> Response:
    membership = await _target_membership(session, principal.org_id, user_id)
    _require_can_assign(principal, membership.role)
    await _guard_last_owner(session, membership)
    await session.delete(membership)
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.org_id == principal.org_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
