from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agentarena_common.auth import CanRead, CanWrite, InternalAccess, Principal
from agentarena_common.db import get_session, utcnow
from agentarena_common.ratelimit import enforce
from scenario_service import starter_pack
from scenario_service.config import Settings
from scenario_service.models import Dataset, Scenario, ScenarioVersion, Suite, SuiteScenario
from scenario_service.schemas import (
    DatasetCreate,
    DatasetList,
    DatasetOut,
    DatasetUpdate,
    EvaluationCriteria,
    ExpectedOutcome,
    ResolvedScenario,
    ResolveResponse,
    ScenarioCreate,
    ScenarioDefinition,
    ScenarioList,
    ScenarioOut,
    ScenarioSummary,
    ScenarioUpdate,
    StarterPackResponse,
    SuiteCreate,
    SuiteItemIn,
    SuiteItemOut,
    SuiteList,
    SuiteOut,
    SuiteUpdate,
    VariationRequest,
    VariationResponse,
    VersionCreate,
    VersionOut,
)
from scenario_service.variations import perturbations, substitute

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _limit(request: Request, principal: Principal) -> None:
    enforce(
        request.app.state.write_limiter,
        str(principal.org_id),
        service=_settings(request).service_name,
        scope="write",
    )


def _conflict(what: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=f"A {what} with this name already exists")


async def _load_scenario(session: AsyncSession, org_id: uuid.UUID, scenario_id: uuid.UUID) -> Scenario:
    scenario = await session.scalar(
        select(Scenario)
        .where(Scenario.id == scenario_id, Scenario.org_id == org_id, Scenario.deleted_at.is_(None))
        .options(selectinload(Scenario.versions))
    )
    if scenario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    return scenario


def _scenario_out(scenario: Scenario) -> ScenarioOut:
    latest = max(scenario.versions, key=lambda version: version.version)
    summary = ScenarioSummary.model_validate(scenario).model_dump()
    return ScenarioOut(**summary, current=VersionOut.model_validate(latest))


def _content(definition: Any, expected: Any, criteria: Any) -> dict[str, dict[str, Any]]:
    """Normalize through the schemas so stored JSON always has every field."""
    return {
        "definition": ScenarioDefinition.model_validate(definition).model_dump(mode="json"),
        "expected": ExpectedOutcome.model_validate(expected).model_dump(mode="json"),
        "criteria": EvaluationCriteria.model_validate(criteria).model_dump(mode="json"),
    }


async def _insert_scenario(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    description: str,
    scenario_type: str,
    difficulty: str,
    tags: list[str],
    content: dict[str, dict[str, Any]],
    parent_scenario_id: uuid.UUID | None = None,
) -> Scenario:
    scenario = Scenario(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        name=name.strip(),
        description=description,
        scenario_type=scenario_type,
        difficulty=difficulty,
        tags=tags,
        parent_scenario_id=parent_scenario_id,
        created_by=principal.user_id,
    )
    session.add(scenario)
    await session.flush()
    session.add(
        ScenarioVersion(
            scenario_id=scenario.id,
            org_id=principal.org_id,
            version=1,
            created_by=principal.user_id,
            change_note="Initial version",
            **content,
        )
    )
    return scenario


@router.post("/v1/scenarios", response_model=ScenarioOut, status_code=status.HTTP_201_CREATED, tags=["scenarios"])
async def create_scenario(body: ScenarioCreate, request: Request, session: Session, principal: CanWrite) -> ScenarioOut:
    _limit(request, principal)
    try:
        scenario = await _insert_scenario(
            session,
            principal,
            name=body.name,
            description=body.description,
            scenario_type=body.scenario_type,
            difficulty=body.difficulty,
            tags=body.tags,
            content=_content(body.definition, body.expected, body.criteria),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("scenario") from exc
    return _scenario_out(await _load_scenario(session, principal.org_id, scenario.id))


@router.get("/v1/scenarios", response_model=ScenarioList, tags=["scenarios"])
async def list_scenarios(
    session: Session,
    principal: CanRead,
    scenario_type: str | None = None,
    difficulty: str | None = None,
    tag: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScenarioList:
    conditions = [Scenario.org_id == principal.org_id, Scenario.deleted_at.is_(None)]
    if scenario_type:
        conditions.append(Scenario.scenario_type == scenario_type)
    if difficulty:
        conditions.append(Scenario.difficulty == difficulty)
    if tag:
        conditions.append(Scenario.tags.any(tag))
    if q:
        conditions.append(Scenario.name.ilike(f"%{q}%"))
    total = await session.scalar(select(func.count()).select_from(Scenario).where(*conditions))
    rows = await session.scalars(
        select(Scenario).where(*conditions).order_by(Scenario.created_at.desc()).limit(limit).offset(offset)
    )
    return ScenarioList(items=[ScenarioSummary.model_validate(row) for row in rows], total=total or 0)


@router.get("/v1/scenarios/resolve", response_model=ResolveResponse, tags=["scenarios"])
async def resolve_scenarios(
    session: Session,
    principal: CanRead,
    ids: Annotated[list[uuid.UUID], Query(max_length=200)],
) -> ResolveResponse:
    """Pins each scenario to its latest version for a run. A read, so it is safe to retry."""
    rows = (
        await session.scalars(
            select(Scenario).where(
                Scenario.id.in_(ids), Scenario.org_id == principal.org_id, Scenario.deleted_at.is_(None)
            )
        )
    ).all()
    found = {row.id: row for row in rows}
    missing = [str(scenario_id) for scenario_id in ids if scenario_id not in found]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Scenarios not found: {', '.join(missing)}")
    return ResolveResponse(
        items=[
            ResolvedScenario(
                scenario_id=scenario_id,
                version=found[scenario_id].latest_version,
                name=found[scenario_id].name,
                scenario_type=found[scenario_id].scenario_type,
                difficulty=found[scenario_id].difficulty,
            )
            for scenario_id in ids
        ]
    )


@router.post("/v1/scenarios/starter-pack", response_model=StarterPackResponse, tags=["scenarios"])
async def install_starter_pack(request: Request, session: Session, principal: CanWrite) -> StarterPackResponse:
    """Creates a complete benchmark for this organization. Safe to call twice."""
    _limit(request, principal)
    existing_suite = await session.scalar(
        select(Suite).where(
            Suite.org_id == principal.org_id, Suite.name == starter_pack.SUITE_NAME, Suite.deleted_at.is_(None)
        )
    )
    existing_dataset = await session.scalar(
        select(Dataset).where(
            Dataset.org_id == principal.org_id, Dataset.name == starter_pack.DATASET_NAME, Dataset.deleted_at.is_(None)
        )
    )
    if existing_suite is not None and existing_dataset is not None:
        return StarterPackResponse(scenarios_created=0, suite_id=existing_suite.id, dataset_id=existing_dataset.id)

    created: list[Scenario] = []
    try:
        for spec in [*starter_pack.SCENARIOS, starter_pack.TEMPLATE_SCENARIO]:
            created.append(
                await _insert_scenario(
                    session,
                    principal,
                    name=spec["name"],
                    description=spec["description"],
                    scenario_type=spec["scenario_type"],
                    difficulty=spec["difficulty"],
                    tags=spec["tags"],
                    content=_content(spec["definition"], spec["expected"], spec.get("criteria", {})),
                )
            )
        suite = Suite(
            id=uuid.uuid4(),
            org_id=principal.org_id,
            name=starter_pack.SUITE_NAME,
            description=starter_pack.SUITE_DESCRIPTION,
            created_by=principal.user_id,
        )
        session.add(suite)
        await session.flush()
        for position, scenario in enumerate(created[: len(starter_pack.SCENARIOS)]):
            session.add(SuiteScenario(suite_id=suite.id, scenario_id=scenario.id, position=position))
        dataset = Dataset(
            id=uuid.uuid4(),
            org_id=principal.org_id,
            name=starter_pack.DATASET_NAME,
            description=starter_pack.DATASET_DESCRIPTION,
            rows=starter_pack.DATASET_ROWS,
            created_by=principal.user_id,
        )
        session.add(dataset)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Some starter pack names are already used in this organization"
        ) from exc
    return StarterPackResponse(scenarios_created=len(created), suite_id=suite.id, dataset_id=dataset.id)


@router.get("/v1/scenarios/{scenario_id}", response_model=ScenarioOut, tags=["scenarios"])
async def get_scenario(scenario_id: uuid.UUID, session: Session, principal: CanRead) -> ScenarioOut:
    return _scenario_out(await _load_scenario(session, principal.org_id, scenario_id))


@router.patch("/v1/scenarios/{scenario_id}", response_model=ScenarioOut, tags=["scenarios"])
async def update_scenario(
    scenario_id: uuid.UUID, body: ScenarioUpdate, request: Request, session: Session, principal: CanWrite
) -> ScenarioOut:
    _limit(request, principal)
    scenario = await _load_scenario(session, principal.org_id, scenario_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(scenario, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("scenario") from exc
    return _scenario_out(await _load_scenario(session, principal.org_id, scenario_id))


@router.delete("/v1/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["scenarios"])
async def delete_scenario(scenario_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> Response:
    """Soft delete, because past runs must keep their exact scenario version."""
    _limit(request, principal)
    scenario = await _load_scenario(session, principal.org_id, scenario_id)
    scenario.deleted_at = utcnow()
    await session.execute(delete(SuiteScenario).where(SuiteScenario.scenario_id == scenario.id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/scenarios/{scenario_id}/versions", response_model=list[VersionOut], tags=["scenarios"])
async def list_versions(scenario_id: uuid.UUID, session: Session, principal: CanRead) -> list[ScenarioVersion]:
    scenario = await _load_scenario(session, principal.org_id, scenario_id)
    return sorted(scenario.versions, key=lambda version: version.version, reverse=True)


@router.post(
    "/v1/scenarios/{scenario_id}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
    tags=["scenarios"],
)
async def create_version(
    scenario_id: uuid.UUID, body: VersionCreate, request: Request, session: Session, principal: CanWrite
) -> ScenarioVersion:
    _limit(request, principal)
    scenario = await session.scalar(
        select(Scenario)
        .where(Scenario.id == scenario_id, Scenario.org_id == principal.org_id, Scenario.deleted_at.is_(None))
        .with_for_update()
    )
    if scenario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    scenario.latest_version += 1
    version = ScenarioVersion(
        scenario_id=scenario.id,
        org_id=principal.org_id,
        version=scenario.latest_version,
        change_note=body.change_note,
        created_by=principal.user_id,
        **_content(body.definition, body.expected, body.criteria),
    )
    session.add(version)
    await session.commit()
    return version


@router.get("/v1/scenarios/{scenario_id}/versions/{version}", response_model=VersionOut, tags=["scenarios"])
async def get_version(scenario_id: uuid.UUID, version: int, session: Session, principal: CanRead) -> ScenarioVersion:
    scenario = await _load_scenario(session, principal.org_id, scenario_id)
    for candidate in scenario.versions:
        if candidate.version == version:
            return candidate
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found")


@router.post(
    "/v1/scenarios/{scenario_id}/variations",
    response_model=VariationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["scenarios"],
)
async def generate_variations(
    scenario_id: uuid.UUID, body: VariationRequest, request: Request, session: Session, principal: CanWrite
) -> VariationResponse:
    _limit(request, principal)
    settings = _settings(request)
    if body.count > settings.max_variations_per_request:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Too many variations requested")
    scenario = await _load_scenario(session, principal.org_id, scenario_id)
    latest = max(scenario.versions, key=lambda version: version.version)

    variants: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    if body.dataset_id is not None:
        dataset = await session.scalar(
            select(Dataset).where(
                Dataset.id == body.dataset_id, Dataset.org_id == principal.org_id, Dataset.deleted_at.is_(None)
            )
        )
        if dataset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset not found")
        for index, row in enumerate(dataset.rows[: body.count], start=1):
            variants.append((f"row {index}", substitute(latest.definition, row), substitute(latest.expected, row)))
    else:
        for strategy, definition in perturbations(latest.definition, body.strategies, body.count, body.seed):
            variants.append((strategy, definition, latest.expected))

    created: list[Scenario] = []
    try:
        for index, (label, definition, expected) in enumerate(variants, start=1):
            created.append(
                await _insert_scenario(
                    session,
                    principal,
                    name=f"{scenario.name} [{label} #{index} seed {body.seed}]",
                    description=f"Generated from '{scenario.name}' using {label}.",
                    scenario_type=scenario.scenario_type,
                    difficulty=scenario.difficulty,
                    tags=sorted({*(tag for tag in scenario.tags if tag != "template"), "generated"}),
                    content=_content(definition, expected, latest.criteria),
                    parent_scenario_id=scenario.id,
                )
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="These variations already exist. Use a different seed."
        ) from exc
    return VariationResponse(created=[ScenarioSummary.model_validate(item) for item in created])


async def _load_suite(session: AsyncSession, org_id: uuid.UUID, suite_id: uuid.UUID) -> Suite:
    suite = await session.scalar(
        select(Suite)
        .where(Suite.id == suite_id, Suite.org_id == org_id, Suite.deleted_at.is_(None))
        .options(selectinload(Suite.items).selectinload(SuiteScenario.scenario))
    )
    if suite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Suite not found")
    return suite


def _suite_out(suite: Suite) -> SuiteOut:
    return SuiteOut(
        id=suite.id,
        org_id=suite.org_id,
        name=suite.name,
        description=suite.description,
        created_at=suite.created_at,
        updated_at=suite.updated_at,
        items=[
            SuiteItemOut(
                scenario_id=item.scenario_id,
                name=item.scenario.name,
                scenario_type=item.scenario.scenario_type,
                difficulty=item.scenario.difficulty,
                pinned_version=item.pinned_version,
                latest_version=item.scenario.latest_version,
            )
            for item in suite.items
        ],
    )


async def _replace_items(session: AsyncSession, suite: Suite, org_id: uuid.UUID, items: list[SuiteItemIn]) -> None:
    ids = [item.scenario_id for item in items]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A scenario can only appear once per suite")
    owned = set(
        await session.scalars(
            select(Scenario.id).where(Scenario.id.in_(ids), Scenario.org_id == org_id, Scenario.deleted_at.is_(None))
        )
    )
    if missing := [str(scenario_id) for scenario_id in ids if scenario_id not in owned]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Scenarios not found: {', '.join(missing)}")
    await session.execute(delete(SuiteScenario).where(SuiteScenario.suite_id == suite.id))
    for position, item in enumerate(items):
        session.add(
            SuiteScenario(
                suite_id=suite.id,
                scenario_id=item.scenario_id,
                pinned_version=item.pinned_version,
                position=position,
            )
        )


@router.post("/v1/suites", response_model=SuiteOut, status_code=status.HTTP_201_CREATED, tags=["suites"])
async def create_suite(body: SuiteCreate, request: Request, session: Session, principal: CanWrite) -> SuiteOut:
    _limit(request, principal)
    suite = Suite(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        name=body.name.strip(),
        description=body.description,
        created_by=principal.user_id,
    )
    session.add(suite)
    try:
        await session.flush()
        await _replace_items(session, suite, principal.org_id, body.items)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("suite") from exc
    return _suite_out(await _load_suite(session, principal.org_id, suite.id))


@router.get("/v1/suites", response_model=SuiteList, tags=["suites"])
async def list_suites(
    session: Session,
    principal: CanRead,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuiteList:
    conditions = [Suite.org_id == principal.org_id, Suite.deleted_at.is_(None)]
    total = await session.scalar(select(func.count()).select_from(Suite).where(*conditions))
    rows = await session.scalars(
        select(Suite)
        .where(*conditions)
        .options(selectinload(Suite.items).selectinload(SuiteScenario.scenario))
        .order_by(Suite.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return SuiteList(items=[_suite_out(row) for row in rows], total=total or 0)


@router.get("/v1/suites/{suite_id}", response_model=SuiteOut, tags=["suites"])
async def get_suite(suite_id: uuid.UUID, session: Session, principal: CanRead) -> SuiteOut:
    return _suite_out(await _load_suite(session, principal.org_id, suite_id))


@router.patch("/v1/suites/{suite_id}", response_model=SuiteOut, tags=["suites"])
async def update_suite(
    suite_id: uuid.UUID, body: SuiteUpdate, request: Request, session: Session, principal: CanWrite
) -> SuiteOut:
    _limit(request, principal)
    suite = await _load_suite(session, principal.org_id, suite_id)
    if body.name is not None:
        suite.name = body.name.strip()
    if body.description is not None:
        suite.description = body.description
    try:
        if body.items is not None:
            await _replace_items(session, suite, principal.org_id, body.items)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("suite") from exc
    session.expire_all()
    return _suite_out(await _load_suite(session, principal.org_id, suite_id))


@router.delete("/v1/suites/{suite_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["suites"])
async def delete_suite(suite_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> Response:
    _limit(request, principal)
    suite = await _load_suite(session, principal.org_id, suite_id)
    suite.deleted_at = utcnow()
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/suites/{suite_id}/resolve", response_model=ResolveResponse, tags=["suites"])
async def resolve_suite(suite_id: uuid.UUID, session: Session, principal: CanRead) -> ResolveResponse:
    """Returns each scenario at the exact version a run should execute."""
    suite = await _load_suite(session, principal.org_id, suite_id)
    return ResolveResponse(
        items=[
            ResolvedScenario(
                scenario_id=item.scenario_id,
                version=item.pinned_version or item.scenario.latest_version,
                name=item.scenario.name,
                scenario_type=item.scenario.scenario_type,
                difficulty=item.scenario.difficulty,
            )
            for item in suite.items
            if item.scenario.deleted_at is None
        ]
    )


def _check_rows(request: Request, rows: list[dict[str, Any]]) -> None:
    if len(rows) > _settings(request).max_dataset_rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Too many rows in dataset")


@router.post("/v1/datasets", response_model=DatasetOut, status_code=status.HTTP_201_CREATED, tags=["datasets"])
async def create_dataset(body: DatasetCreate, request: Request, session: Session, principal: CanWrite) -> Dataset:
    _limit(request, principal)
    _check_rows(request, body.rows)
    dataset = Dataset(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        name=body.name.strip(),
        description=body.description,
        rows=body.rows,
        created_by=principal.user_id,
    )
    session.add(dataset)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("dataset") from exc
    await session.refresh(dataset)
    return dataset


@router.get("/v1/datasets", response_model=DatasetList, tags=["datasets"])
async def list_datasets(
    session: Session,
    principal: CanRead,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DatasetList:
    conditions = [Dataset.org_id == principal.org_id, Dataset.deleted_at.is_(None)]
    total = await session.scalar(select(func.count()).select_from(Dataset).where(*conditions))
    rows = await session.scalars(
        select(Dataset).where(*conditions).order_by(Dataset.created_at.desc()).limit(limit).offset(offset)
    )
    return DatasetList(items=[DatasetOut.model_validate(row) for row in rows], total=total or 0)


async def _load_dataset(session: AsyncSession, org_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = await session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == org_id, Dataset.deleted_at.is_(None))
    )
    if dataset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return dataset


@router.get("/v1/datasets/{dataset_id}", response_model=DatasetOut, tags=["datasets"])
async def get_dataset(dataset_id: uuid.UUID, session: Session, principal: CanRead) -> Dataset:
    return await _load_dataset(session, principal.org_id, dataset_id)


@router.patch("/v1/datasets/{dataset_id}", response_model=DatasetOut, tags=["datasets"])
async def update_dataset(
    dataset_id: uuid.UUID, body: DatasetUpdate, request: Request, session: Session, principal: CanWrite
) -> Dataset:
    _limit(request, principal)
    dataset = await _load_dataset(session, principal.org_id, dataset_id)
    if body.rows is not None:
        _check_rows(request, body.rows)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(dataset, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("dataset") from exc
    await session.refresh(dataset)
    return dataset


@router.delete("/v1/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["datasets"])
async def delete_dataset(dataset_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> Response:
    _limit(request, principal)
    dataset = await _load_dataset(session, principal.org_id, dataset_id)
    dataset.deleted_at = utcnow()
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/internal/v1/scenarios/{scenario_id}/versions/{version}", include_in_schema=False)
async def internal_version(
    scenario_id: uuid.UUID, version: int, session: Session, caller: InternalAccess
) -> dict[str, Any]:
    """Used by workers. Soft-deleted scenarios stay readable so old runs can replay."""
    row = await session.scalar(
        select(ScenarioVersion)
        .where(
            ScenarioVersion.scenario_id == scenario_id,
            ScenarioVersion.version == version,
            ScenarioVersion.org_id == caller.org_id,
        )
        .options(selectinload(ScenarioVersion.scenario))
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Scenario version not found")
    return {
        "scenario_id": str(row.scenario_id),
        "version": row.version,
        "name": row.scenario.name,
        "scenario_type": row.scenario.scenario_type,
        "difficulty": row.scenario.difficulty,
        "definition": row.definition,
        "expected": row.expected,
        "criteria": row.criteria,
    }
