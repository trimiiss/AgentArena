from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from agentarena_common.db import Database, utcnow
from agentarena_common.events import (
    EvaluationRequested,
    ExecutionMetrics,
    RunCompleted,
    TranscriptStep,
    make_event,
)
from evaluation_service.config import Settings
from evaluation_service.consumer import EvaluationConsumer
from evaluation_service.judge import JudgeOutcome, JudgeVerdict
from evaluation_service.models import Benchmark, Evaluation, EvaluatorResult, JudgeResult, Outbox

pytestmark = pytest.mark.integration


def request(org: uuid.UUID, *, run_id: uuid.UUID | None = None, judge: bool = False, passed: bool = True):
    reply = "Your order has shipped." if passed else "I do not know."
    payload = EvaluationRequested(
        run_id=run_id or uuid.uuid4(),
        run_name="run",
        execution_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        agent_name="agent",
        agent_version=1,
        scenario_id=uuid.uuid4(),
        scenario_version=1,
        scenario_name="scenario",
        scenario_type="tool_use",
        difficulty="easy",
        repetition=1,
        seed=1,
        execution_status="completed",
        expected={"required_phrases": ["shipped"]},
        transcript=[TranscriptStep(seq=1, type="agent_response", content=reply, task_status="completed")],
        metrics=ExecutionMetrics(latency_ms=100, input_tokens=5, output_tokens=5),
        judge_enabled=judge,
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    return make_event("evaluation.requested", payload, producer="test", org_id=org, correlation_id="c")


async def count(database: Database, model: Any) -> int:
    async with database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_evaluation_commits_with_its_outgoing_event(evaluations_database: Database, org_id: uuid.UUID) -> None:
    consumer = EvaluationConsumer(Settings(), evaluations_database)
    await consumer.handle(request(org_id), None)
    assert await count(evaluations_database, Evaluation) == 1
    assert await count(evaluations_database, EvaluatorResult) > 0
    async with evaluations_database.sessions() as session:
        outbox = (await session.scalars(select(Outbox))).all()
    assert [row.routing_key for row in outbox] == ["evaluation.completed"]
    assert outbox[0].payload["correlation_id"] == "c"


async def test_redelivered_request_changes_nothing(evaluations_database: Database, org_id: uuid.UUID) -> None:
    consumer = EvaluationConsumer(Settings(), evaluations_database)
    event = request(org_id)
    await consumer.handle(event, None)
    await consumer.handle(event, None)
    assert await count(evaluations_database, Evaluation) == 1
    assert await count(evaluations_database, Outbox) == 1


async def test_a_new_event_for_the_same_execution_is_also_rejected(
    evaluations_database: Database, org_id: uuid.UUID
) -> None:
    """The unique constraint backs up deduplication if a producer sends two events."""
    consumer = EvaluationConsumer(Settings(), evaluations_database)
    first = request(org_id)
    second = first.model_copy(update={"event_id": uuid.uuid4()})
    await consumer.handle(first, None)
    await consumer.handle(second, None)
    assert await count(evaluations_database, Evaluation) == 1


class StubJudge:
    model = "stub-model"

    def __init__(self, outcome: JudgeOutcome | Exception) -> None:
        self.outcome = outcome

    async def review(self, **_: Any) -> JudgeOutcome:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def test_judge_is_stored_separately_and_does_not_change_the_score(
    evaluations_database: Database, org_id: uuid.UUID
) -> None:
    verdict = JudgeVerdict(
        relevance=1, reasoning_quality=1, context_consistency=1, conversation_quality=1, error_analysis="Poor."
    )
    consumer = EvaluationConsumer(Settings(), evaluations_database, StubJudge(JudgeOutcome("completed", verdict)))
    await consumer.handle(request(org_id, judge=True), None)
    async with evaluations_database.sessions() as session:
        evaluation = await session.scalar(select(Evaluation))
        judgement = await session.scalar(select(JudgeResult))
    assert evaluation.passed is True
    assert evaluation.score == 1.0
    assert judgement.relevance == 1


async def test_judge_runs_only_when_requested(evaluations_database: Database, org_id: uuid.UUID) -> None:
    consumer = EvaluationConsumer(Settings(), evaluations_database, StubJudge(JudgeOutcome("completed")))
    await consumer.handle(request(org_id, judge=False), None)
    assert await count(evaluations_database, JudgeResult) == 0


async def test_failing_judge_never_blocks_the_pipeline(evaluations_database: Database, org_id: uuid.UUID) -> None:
    consumer = EvaluationConsumer(Settings(), evaluations_database, StubJudge(RuntimeError("provider down")))
    await consumer.handle(request(org_id, judge=True), None)
    assert await count(evaluations_database, Evaluation) == 1
    assert await count(evaluations_database, Outbox) == 1


async def test_benchmark_is_built_from_every_evaluation(evaluations_database: Database, org_id: uuid.UUID) -> None:
    consumer = EvaluationConsumer(Settings(), evaluations_database)
    run_id = uuid.uuid4()
    for passed in (True, True, False):
        await consumer.handle(request(org_id, run_id=run_id, passed=passed), None)
    completed = RunCompleted(
        run_id=run_id,
        agent_id=uuid.uuid4(),
        agent_version=1,
        status="completed",
        total_executions=3,
        finished_at=utcnow(),
    )
    event = make_event("run.completed", completed, producer="test", org_id=org_id)
    await consumer.handle(event, None)
    await consumer.handle(event, None)
    async with evaluations_database.sessions() as session:
        benchmark = await session.get(Benchmark, run_id)
    assert benchmark.summary["totals"]["executions"] == 3
    assert benchmark.pass_rate == pytest.approx(0.6667)
    assert benchmark.verdict == "failed"
