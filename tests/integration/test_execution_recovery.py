"""Claims, leases and recovery. These rules stop work being lost or done twice."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from conftest import execution_values, run_values
from sqlalchemy import func, select

from agentarena_common.db import Database, utcnow
from agentarena_common.events import EventEnvelope, ExecutionRequested, make_event
from run_orchestrator.config import Settings
from run_orchestrator.engine import Outcome
from run_orchestrator.lifecycle import dispatch, maybe_finish
from run_orchestrator.models import Execution, Outbox, Run
from run_orchestrator.scheduler import Scheduler
from run_orchestrator.worker import Worker

pytestmark = pytest.mark.integration


class FakeDependencies:
    async def load_agent_config(self, *_: Any) -> dict[str, Any]:
        return {}

    async def load_scenario(self, *_: Any) -> dict[str, Any]:
        return {}


def make_settings(**overrides: Any) -> Settings:
    return Settings(lease_seconds=0.3, retry_base_seconds=0, retry_max_seconds=0, **overrides)


def make_worker(database: Database, worker_id: str) -> Worker:
    client = httpx.AsyncClient()
    return Worker(make_settings(), database, FakeDependencies(), client, worker_id=worker_id)  # type: ignore[arg-type]


async def seed(
    database: Database, org: uuid.UUID, *, executions: int = 1, run: dict | None = None, **execution: Any
) -> tuple[Run, list[Execution]]:
    async with database.sessions() as session, session.begin():
        row = Run(**run_values(org, total_executions=executions, **(run or {})))
        session.add(row)
        await session.flush()
        rows = [Execution(**execution_values(row, index, **execution)) for index in range(1, executions + 1)]
        session.add_all(rows)
    return row, rows


def job(run: Run, execution: Execution, attempt: int = 1) -> tuple[EventEnvelope, ExecutionRequested]:
    payload = ExecutionRequested(run_id=run.id, execution_id=execution.id, attempt=attempt, dispatched_at=utcnow())
    return make_event("execution.requested", payload, producer="test", org_id=run.org_id), payload


async def reload(database: Database, execution_id: uuid.UUID) -> Execution:
    async with database.sessions() as session:
        return await session.get(Execution, execution_id)


async def outbox_types(database: Database) -> list[str]:
    async with database.sessions() as session:
        return list(await session.scalars(select(Outbox.routing_key).order_by(Outbox.id)))


class TestClaim:
    async def test_claim_moves_the_execution_to_running(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", dispatched_at=utcnow())
        worker = make_worker(runs_database, "w1")
        claim = await worker._claim(*job(run, execution))
        assert claim is not None
        row = await reload(runs_database, execution.id)
        assert row.status == "running"
        assert row.worker_id == "w1"
        assert row.lease_expires_at is not None
        assert "execution.started" in await outbox_types(runs_database)

    async def test_concurrent_workers_cannot_both_claim(self, runs_database: Database, org_id: uuid.UUID) -> None:
        """Eight workers receive the same job at once and exactly one wins."""
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", dispatched_at=utcnow())
        workers = [make_worker(runs_database, f"w{index}") for index in range(8)]
        jobs = [job(run, execution) for _ in workers]
        claims = await asyncio.gather(*(worker._claim(*item) for worker, item in zip(workers, jobs, strict=True)))
        assert sum(claim is not None for claim in claims) == 1

    async def test_redelivered_message_is_ignored(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", dispatched_at=utcnow())
        worker = make_worker(runs_database, "w1")
        message = job(run, execution)
        assert await worker._claim(*message) is not None
        assert await worker._claim(*message) is None

    async def test_job_from_an_old_attempt_is_ignored(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", attempt=2, dispatched_at=utcnow())
        assert await make_worker(runs_database, "w1")._claim(*job(run, execution, attempt=1)) is None

    async def test_job_for_a_cancelled_run_is_dropped(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, (execution,) = await seed(
            runs_database, org_id, status="dispatched", dispatched_at=utcnow(), run={"status": "cancelling"}
        )
        assert await make_worker(runs_database, "w1")._claim(*job(run, execution)) is None
        assert (await reload(runs_database, execution.id)).status == "cancelled"


class TestFinish:
    async def test_completed_outcome_requests_evaluation(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", dispatched_at=utcnow())
        worker = make_worker(runs_database, "w1")
        _, payload = job(run, execution)
        claim = await worker._claim(*job(run, execution))
        await worker._finish(payload, claim, Outcome(status="completed", latency_ms=12.5), {"expected": {}})
        row = await reload(runs_database, execution.id)
        assert row.status == "completed"
        assert row.latency_ms == 12.5
        types = await outbox_types(runs_database)
        assert "execution.completed" in types
        assert "evaluation.requested" in types

    async def test_retryable_failure_waits_for_another_attempt(
        self, runs_database: Database, org_id: uuid.UUID
    ) -> None:
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", dispatched_at=utcnow())
        worker = make_worker(runs_database, "w1")
        _, payload = job(run, execution)
        claim = await worker._claim(*job(run, execution))
        outcome = Outcome(status="failed", error_code="agent_http_5xx", error_message="x", retryable=True)
        await worker._finish(payload, claim, outcome, None)
        row = await reload(runs_database, execution.id)
        assert row.status == "retry_wait"
        assert row.next_attempt_at is not None
        assert "evaluation.requested" not in await outbox_types(runs_database)

    async def test_last_attempt_is_terminal_and_still_evaluated(
        self, runs_database: Database, org_id: uuid.UUID
    ) -> None:
        run, (execution,) = await seed(
            runs_database, org_id, status="dispatched", attempt=2, max_attempts=2, dispatched_at=utcnow()
        )
        worker = make_worker(runs_database, "w1")
        _, payload = job(run, execution, attempt=2)
        claim = await worker._claim(*job(run, execution, attempt=2))
        outcome = Outcome(status="timed_out", error_code="agent_timeout", error_message="x", retryable=True)
        await worker._finish(payload, claim, outcome, None)
        assert (await reload(runs_database, execution.id)).status == "timed_out"
        assert "evaluation.requested" in await outbox_types(runs_database)

    async def test_worker_that_lost_its_lease_cannot_write(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, (execution,) = await seed(runs_database, org_id, status="dispatched", dispatched_at=utcnow())
        slow = make_worker(runs_database, "slow")
        _, payload = job(run, execution)
        claim = await slow._claim(*job(run, execution))

        async with runs_database.sessions() as session, session.begin():
            row = await session.get(Execution, execution.id)
            row.status = "running"
            row.worker_id = "replacement"
            row.attempt = 2

        await slow._finish(payload, claim, Outcome(status="completed", latency_ms=999), {"expected": {}})
        row = await reload(runs_database, execution.id)
        assert row.status == "running"
        assert row.worker_id == "replacement"
        assert row.latency_ms is None
        assert "execution.completed" not in await outbox_types(runs_database)


class TestLeaseRecovery:
    def scheduler(self, database: Database, **overrides: Any) -> Scheduler:
        return Scheduler(make_settings(**overrides), database)

    async def test_expired_lease_is_scheduled_for_retry(self, runs_database: Database, org_id: uuid.UUID) -> None:
        _, (execution,) = await seed(
            runs_database,
            org_id,
            status="running",
            worker_id="dead",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        await self.scheduler(runs_database)._recover_expired_leases()
        row = await reload(runs_database, execution.id)
        assert row.status == "retry_wait"
        assert row.worker_id is None

    async def test_live_lease_is_left_alone(self, runs_database: Database, org_id: uuid.UUID) -> None:
        _, (execution,) = await seed(
            runs_database,
            org_id,
            status="running",
            worker_id="alive",
            lease_expires_at=utcnow() + timedelta(seconds=30),
        )
        await self.scheduler(runs_database)._recover_expired_leases()
        assert (await reload(runs_database, execution.id)).status == "running"

    async def test_lost_execution_without_retries_fails_and_is_evaluated(
        self, runs_database: Database, org_id: uuid.UUID
    ) -> None:
        _, (execution,) = await seed(
            runs_database,
            org_id,
            status="running",
            attempt=2,
            max_attempts=2,
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        await self.scheduler(runs_database)._recover_expired_leases()
        row = await reload(runs_database, execution.id)
        assert row.status == "failed"
        assert row.error_code == "worker_lost"
        assert await outbox_types(runs_database) == ["execution.failed", "evaluation.requested"]

    async def test_job_nobody_picked_up_is_recovered(self, runs_database: Database, org_id: uuid.UUID) -> None:
        _, (execution,) = await seed(
            runs_database, org_id, status="dispatched", dispatched_at=utcnow() - timedelta(hours=1)
        )
        await self.scheduler(runs_database, dispatch_timeout_seconds=60)._recover_lost_dispatches()
        assert (await reload(runs_database, execution.id)).status == "retry_wait"

    async def test_retry_is_redispatched_as_a_new_attempt(self, runs_database: Database, org_id: uuid.UUID) -> None:
        _, (execution,) = await seed(runs_database, org_id, status="retry_wait", next_attempt_at=utcnow())
        await self.scheduler(runs_database)._dispatch_due_retries()
        row = await reload(runs_database, execution.id)
        assert row.status == "dispatched"
        assert row.attempt == 2

    async def test_future_retry_waits_for_its_time(self, runs_database: Database, org_id: uuid.UUID) -> None:
        _, (execution,) = await seed(
            runs_database, org_id, status="retry_wait", next_attempt_at=utcnow() + timedelta(minutes=1)
        )
        await self.scheduler(runs_database)._dispatch_due_retries()
        assert (await reload(runs_database, execution.id)).status == "retry_wait"


class TestDispatch:
    async def test_only_free_slots_are_filled(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(runs_database, org_id, executions=10, run={"concurrency": 3})
        async with runs_database.sessions() as session, session.begin():
            locked = await session.scalar(select(Run).where(Run.id == run.id).with_for_update())
            assert await dispatch(session, locked, producer="test") == 3
            assert locked.status == "running"
        async with runs_database.sessions() as session, session.begin():
            locked = await session.scalar(select(Run).where(Run.id == run.id).with_for_update())
            assert await dispatch(session, locked, producer="test") == 0

    async def test_concurrent_schedulers_cannot_exceed_the_limit(
        self, runs_database: Database, org_id: uuid.UUID
    ) -> None:
        run, _ = await seed(runs_database, org_id, executions=20, run={"concurrency": 4})

        async def attempt() -> None:
            async with runs_database.sessions() as session, session.begin():
                locked = await session.scalar(select(Run).where(Run.id == run.id).with_for_update())
                await dispatch(session, locked, producer="test")

        await asyncio.gather(*(attempt() for _ in range(10)))
        async with runs_database.sessions() as session:
            active = await session.scalar(
                select(func.count()).select_from(Execution).where(Execution.status == "dispatched")
            )
        assert active == 4

    async def test_cancelled_run_dispatches_nothing(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(runs_database, org_id, executions=2, run={"status": "cancelling"})
        async with runs_database.sessions() as session, session.begin():
            locked = await session.get(Run, run.id)
            assert await dispatch(session, locked, producer="test") == 0


class TestRunCompletion:
    async def _finish(self, database: Database, run: Run) -> str:
        async with database.sessions() as session, session.begin():
            locked = await session.scalar(select(Run).where(Run.id == run.id).with_for_update())
            await maybe_finish(session, locked, producer="test")
            return locked.status

    async def test_run_waits_for_evaluations(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(runs_database, org_id, status="completed", run={"status": "running"})
        assert await self._finish(runs_database, run) == "evaluating"

    async def test_run_completes_once_everything_is_evaluated(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(
            runs_database, org_id, status="completed", evaluated_at=utcnow(), run={"status": "evaluating"}
        )
        assert await self._finish(runs_database, run) == "completed"
        assert "run.completed" in await outbox_types(runs_database)

    async def test_all_executions_failing_fails_the_run(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(runs_database, org_id, status="failed", evaluated_at=utcnow(), run={"status": "evaluating"})
        assert await self._finish(runs_database, run) == "failed"

    async def test_unfinished_work_keeps_the_run_open(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(runs_database, org_id, status="running", run={"status": "running"})
        assert await self._finish(runs_database, run) == "running"

    async def test_run_finishes_only_once(self, runs_database: Database, org_id: uuid.UUID) -> None:
        run, _ = await seed(
            runs_database, org_id, status="completed", evaluated_at=utcnow(), run={"status": "evaluating"}
        )
        await self._finish(runs_database, run)
        await self._finish(runs_database, run)
        assert (await outbox_types(runs_database)).count("run.completed") == 1
