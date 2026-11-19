from __future__ import annotations

import time
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent_registry.config import Settings
from agent_registry.crypto import CredentialCipher
from agent_registry.models import Agent, AgentCredential, AgentVersion
from agent_registry.schemas import (
    AgentCreate,
    AgentList,
    AgentOut,
    AgentUpdate,
    CredentialIn,
    CredentialOut,
    ResolvedAgent,
    ValidationCheck,
    ValidationResult,
    VersionConfig,
    VersionOut,
)
from agentarena_common.agent_protocol import PROTOCOL_VERSION, AgentRequest, AgentResponse, ChatMessage
from agentarena_common.auth import CanRead, CanWrite, InternalAccess, Principal
from agentarena_common.db import get_session, utcnow
from agentarena_common.ratelimit import enforce
from agentarena_common.ssrf import UnsafeEndpointError, assert_safe_endpoint, parse_allowlist

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _cipher(request: Request) -> CredentialCipher:
    return request.app.state.cipher


def _limit(request: Request, principal: Principal) -> None:
    enforce(
        request.app.state.write_limiter,
        str(principal.org_id),
        service=_settings(request).service_name,
        scope="write",
    )


async def _check_endpoint(request: Request, url: str) -> None:
    settings = _settings(request)
    try:
        await assert_safe_endpoint(
            url,
            allowlist=parse_allowlist(settings.agent_endpoint_allowlist),
            allow_http=settings.allow_http_endpoints,
        )
    except UnsafeEndpointError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _agent_query():
    return select(Agent).options(selectinload(Agent.versions), selectinload(Agent.credential))


async def _load_agent(session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    """Every lookup filters by organization, so other tenants see a plain 404."""
    agent = await session.scalar(
        _agent_query().where(Agent.id == agent_id, Agent.org_id == org_id, Agent.deleted_at.is_(None))
    )
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


def _latest(agent: Agent) -> AgentVersion:
    return max(agent.versions, key=lambda version: version.version)


def _agent_out(agent: Agent) -> AgentOut:
    return AgentOut(
        id=agent.id,
        org_id=agent.org_id,
        name=agent.name,
        description=agent.description,
        agent_type=agent.agent_type,
        status=agent.status,
        latest_version=agent.latest_version,
        endpoint_url=_latest(agent).endpoint_url,
        credential=CredentialOut.model_validate(agent.credential) if agent.credential else None,
        last_validated_at=agent.last_validated_at,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _new_version(agent: Agent, version: int, config: VersionConfig, created_by: uuid.UUID) -> AgentVersion:
    return AgentVersion(
        agent_id=agent.id,
        org_id=agent.org_id,
        version=version,
        endpoint_url=config.endpoint_url,
        integration_type=config.integration_type,
        request_timeout_seconds=config.request_timeout_seconds,
        default_headers=config.default_headers,
        model_name=config.model_name,
        input_cost_per_1k=config.input_cost_per_1k,
        output_cost_per_1k=config.output_cost_per_1k,
        notes=config.notes,
        created_by=created_by,
    )


@router.post("/v1/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED, tags=["agents"])
async def create_agent(body: AgentCreate, request: Request, session: Session, principal: CanWrite) -> AgentOut:
    _limit(request, principal)
    await _check_endpoint(request, body.endpoint_url)
    agent = Agent(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        name=body.name.strip(),
        description=body.description,
        agent_type=body.agent_type,
        created_by=principal.user_id,
    )
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An agent with this name already exists") from exc
    session.add(_new_version(agent, 1, body, principal.user_id))
    await session.commit()
    return _agent_out(await _load_agent(session, principal.org_id, agent.id))


@router.get("/v1/agents", response_model=AgentList, tags=["agents"])
async def list_agents(
    session: Session,
    principal: CanRead,
    agent_status: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentList:
    conditions = [Agent.org_id == principal.org_id, Agent.deleted_at.is_(None)]
    if agent_status:
        conditions.append(Agent.status == agent_status)
    if q:
        conditions.append(Agent.name.ilike(f"%{q}%"))
    total = await session.scalar(select(func.count()).select_from(Agent).where(*conditions))
    rows = await session.scalars(
        _agent_query().where(*conditions).order_by(Agent.created_at.desc()).limit(limit).offset(offset)
    )
    return AgentList(items=[_agent_out(row) for row in rows], total=total or 0)


@router.get("/v1/agents/{agent_id}", response_model=AgentOut, tags=["agents"])
async def get_agent(agent_id: uuid.UUID, session: Session, principal: CanRead) -> AgentOut:
    return _agent_out(await _load_agent(session, principal.org_id, agent_id))


@router.patch("/v1/agents/{agent_id}", response_model=AgentOut, tags=["agents"])
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdate, request: Request, session: Session, principal: CanWrite
) -> AgentOut:
    _limit(request, principal)
    agent = await _load_agent(session, principal.org_id, agent_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An agent with this name already exists") from exc
    return _agent_out(await _load_agent(session, principal.org_id, agent_id))


@router.delete("/v1/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["agents"])
async def delete_agent(agent_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> Response:
    """Soft delete, because past benchmarks must keep pointing at real versions."""
    _limit(request, principal)
    agent = await _load_agent(session, principal.org_id, agent_id)
    agent.deleted_at = utcnow()
    agent.status = "disabled"
    if agent.credential is not None:
        await session.delete(agent.credential)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/agents/{agent_id}/versions", response_model=list[VersionOut], tags=["versions"])
async def list_versions(agent_id: uuid.UUID, session: Session, principal: CanRead) -> list[AgentVersion]:
    agent = await _load_agent(session, principal.org_id, agent_id)
    return sorted(agent.versions, key=lambda version: version.version, reverse=True)


@router.post(
    "/v1/agents/{agent_id}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
    tags=["versions"],
)
async def create_version(
    agent_id: uuid.UUID, body: VersionConfig, request: Request, session: Session, principal: CanWrite
) -> AgentVersion:
    _limit(request, principal)
    await _check_endpoint(request, body.endpoint_url)
    agent = await session.scalar(
        select(Agent)
        .where(Agent.id == agent_id, Agent.org_id == principal.org_id, Agent.deleted_at.is_(None))
        .with_for_update()
    )
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agent not found")
    agent.latest_version += 1
    version = _new_version(agent, agent.latest_version, body, principal.user_id)
    session.add(version)
    await session.commit()
    return version


@router.get("/v1/agents/{agent_id}/versions/{version}", response_model=VersionOut, tags=["versions"])
async def get_version(agent_id: uuid.UUID, version: int, session: Session, principal: CanRead) -> AgentVersion:
    agent = await _load_agent(session, principal.org_id, agent_id)
    for candidate in agent.versions:
        if candidate.version == version:
            return candidate
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found")


@router.put("/v1/agents/{agent_id}/credential", response_model=CredentialOut, tags=["credentials"])
async def put_credential(
    agent_id: uuid.UUID, body: CredentialIn, request: Request, session: Session, principal: CanWrite
) -> AgentCredential:
    _limit(request, principal)
    agent = await _load_agent(session, principal.org_id, agent_id)
    cipher = _cipher(request)
    ciphertext = cipher.encrypt(body.secret, org_id=agent.org_id, agent_id=agent.id)
    credential = agent.credential
    if credential is None:
        credential = AgentCredential(agent_id=agent.id, org_id=agent.org_id, auth_type=body.auth_type)
        session.add(credential)
    credential.auth_type = body.auth_type
    credential.header_name = "Authorization" if body.auth_type == "bearer" else body.header_name
    credential.ciphertext = ciphertext
    credential.key_id = cipher.key_id
    await session.commit()
    await session.refresh(credential)
    return credential


@router.delete("/v1/agents/{agent_id}/credential", status_code=status.HTTP_204_NO_CONTENT, tags=["credentials"])
async def delete_credential(agent_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> Response:
    _limit(request, principal)
    agent = await _load_agent(session, principal.org_id, agent_id)
    if agent.credential is not None:
        await session.delete(agent.credential)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _auth_headers(agent: Agent, cipher: CredentialCipher) -> dict[str, str]:
    credential = agent.credential
    if credential is None:
        return {}
    secret = cipher.decrypt(credential.ciphertext, org_id=agent.org_id, agent_id=agent.id)
    if credential.auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    return {credential.header_name: secret}


@router.post("/v1/agents/{agent_id}/validate", response_model=ValidationResult, tags=["agents"])
async def validate_agent(
    agent_id: uuid.UUID, request: Request, session: Session, principal: CanWrite
) -> ValidationResult:
    """Checks the configuration, then runs a real protocol handshake."""
    _limit(request, principal)
    settings = _settings(request)
    agent = await _load_agent(session, principal.org_id, agent_id)
    version = _latest(agent)
    checks: list[ValidationCheck] = []
    latency_ms: float | None = None

    try:
        await assert_safe_endpoint(
            version.endpoint_url,
            allowlist=parse_allowlist(settings.agent_endpoint_allowlist),
            allow_http=settings.allow_http_endpoints,
        )
        checks.append(ValidationCheck(name="endpoint_policy", passed=True))
    except UnsafeEndpointError as exc:
        checks.append(ValidationCheck(name="endpoint_policy", passed=False, detail=str(exc)))

    if checks[-1].passed:
        probe = AgentRequest(
            session_id=f"probe-{uuid.uuid4().hex}",
            turn=1,
            messages=[ChatMessage(role="user", content="AgentArena connectivity check. Reply with any message.")],
            metadata={"probe": True},
        )
        headers = {**version.default_headers, **_auth_headers(agent, _cipher(request))}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.probe_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(version.endpoint_url, json=probe.model_dump(mode="json"), headers=headers)
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            checks.append(
                ValidationCheck(
                    name="connectivity", passed=response.status_code == 200, detail=f"HTTP {response.status_code}"
                )
            )
            if response.status_code == 200:
                try:
                    parsed = AgentResponse.model_validate(response.json())
                    compatible = parsed.protocol_version.split(".")[0] == PROTOCOL_VERSION.split(".")[0]
                    checks.append(
                        ValidationCheck(
                            name="protocol",
                            passed=compatible,
                            detail=f"Agent speaks protocol {parsed.protocol_version}",
                        )
                    )
                except (ValueError, ValidationError) as exc:
                    checks.append(
                        ValidationCheck(name="protocol", passed=False, detail=f"Invalid response: {str(exc)[:200]}")
                    )
        except httpx.HTTPError as exc:
            checks.append(ValidationCheck(name="connectivity", passed=False, detail=type(exc).__name__))

    valid = all(check.passed for check in checks)
    agent.last_validated_at = utcnow()
    if agent.status != "disabled":
        agent.status = "active" if valid else "unreachable"
    await session.commit()
    return ValidationResult(
        valid=valid, status=agent.status, version=version.version, latency_ms=latency_ms, checks=checks
    )


@router.get(
    "/internal/v1/agents/{agent_id}/versions/{version}",
    response_model=ResolvedAgent,
    include_in_schema=False,
)
async def resolve_agent(
    agent_id: uuid.UUID, version: int, request: Request, session: Session, caller: InternalAccess
) -> ResolvedAgent:
    """Used by workers. Requires the internal token and stays scoped to one organization."""
    agent = await session.scalar(_agent_query().where(Agent.id == agent_id, Agent.org_id == caller.org_id))
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Agent not found")
    selected = next((v for v in agent.versions if v.version == version), None)
    if selected is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found")
    return ResolvedAgent(
        agent_id=agent.id,
        version=selected.version,
        endpoint_url=selected.endpoint_url,
        integration_type=selected.integration_type,
        request_timeout_seconds=selected.request_timeout_seconds,
        headers={**selected.default_headers, **_auth_headers(agent, _cipher(request))},
        model_name=selected.model_name,
        input_cost_per_1k=float(selected.input_cost_per_1k) if selected.input_cost_per_1k is not None else None,
        output_cost_per_1k=float(selected.output_cost_per_1k) if selected.output_cost_per_1k is not None else None,
    )
