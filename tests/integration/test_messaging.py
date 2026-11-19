"""Outbox, retries, dead-lettering and deduplication against a real broker."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import aio_pika
import pytest
from sqlalchemy import func, select

from agentarena_common.db import Database
from agentarena_common.events import EventEnvelope, RunRequested, make_event
from agentarena_common.idempotency import first_delivery
from agentarena_common.messaging import MessageBus, PoisonMessage, QueueSpec
from agentarena_common.outbox import OutboxRelay, enqueue
from run_orchestrator.models import Outbox, ProcessedMessage

pytestmark = pytest.mark.integration


def unique_spec(*, retries: tuple[int, ...] = (1,)) -> tuple[QueueSpec, str]:
    routing_key = f"test.{uuid.uuid4().hex}"
    return QueueSpec(f"test.queue.{uuid.uuid4().hex}", (routing_key,), retry_delays_seconds=retries), routing_key


def event(org: uuid.UUID) -> EventEnvelope:
    return make_event(
        "run.requested",
        RunRequested(run_id=uuid.uuid4(), total_executions=1, concurrency=1),
        producer="test",
        org_id=org,
        correlation_id="test-correlation",
    )


def relay_for(database: Database, bus: MessageBus) -> OutboxRelay:
    return OutboxRelay(database, bus, Outbox, service="test")


async def publish(bus: MessageBus, routing_key: str, envelope: EventEnvelope) -> None:
    await bus.publish_raw(routing_key, envelope.model_dump_json().encode(), {}, str(envelope.event_id))


async def drain(
    bus: MessageBus, queue_name: str, *, timeout: float = 5.0
) -> list[aio_pika.abc.AbstractIncomingMessage]:
    """Read a queue directly, without the consumer logic under test."""
    assert bus.channel is not None
    queue = await bus.channel.get_queue(queue_name, ensure=False)
    received = []
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        message = await queue.get(fail=False)
        if message is not None:
            await message.ack()
            received.append(message)
            return received
        await asyncio.sleep(0.1)
    return received


async def read_all(bus: MessageBus, queue_name: str) -> list[str]:
    assert bus.channel is not None
    queue = await bus.channel.get_queue(queue_name, ensure=False)
    ids = []
    while (message := await queue.get(fail=False)) is not None:
        await message.ack()
        ids.append(json.loads(message.body)["event_id"])
    return ids


async def cleanup(bus: MessageBus, spec: QueueSpec) -> None:
    assert bus.channel is not None
    await bus.stop_consuming()
    names = [spec.name, spec.dlq, *(spec.retry_queue(i + 1) for i in range(len(spec.retry_delays_seconds)))]
    for name in names:
        await bus.channel.queue_delete(name)


class TestOutbox:
    async def test_rolled_back_transaction_leaves_no_event(self, runs_database: Database, org_id: uuid.UUID) -> None:
        with pytest.raises(RuntimeError):
            async with runs_database.sessions() as session, session.begin():
                enqueue(session, Outbox, event(org_id))
                raise RuntimeError("business rule failed after writing")
        async with runs_database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Outbox)) == 0

    async def test_committed_event_reaches_the_broker(
        self, runs_database: Database, bus: MessageBus, org_id: uuid.UUID
    ) -> None:
        spec, routing_key = unique_spec()
        await bus.declare(spec)
        envelope = event(org_id)
        async with runs_database.sessions() as session, session.begin():
            session.add(
                Outbox(event_id=envelope.event_id, routing_key=routing_key, payload=envelope.model_dump(mode="json"))
            )

        assert await relay_for(runs_database, bus).publish_batch() == 1

        messages = await drain(bus, spec.name)
        assert len(messages) == 1
        assert json.loads(messages[0].body)["event_id"] == str(envelope.event_id)
        assert messages[0].delivery_mode == aio_pika.DeliveryMode.PERSISTENT
        await cleanup(bus, spec)

    async def test_published_rows_are_not_sent_again(
        self, runs_database: Database, bus: MessageBus, org_id: uuid.UUID
    ) -> None:
        spec, routing_key = unique_spec()
        await bus.declare(spec)
        envelope = event(org_id)
        async with runs_database.sessions() as session, session.begin():
            session.add(
                Outbox(event_id=envelope.event_id, routing_key=routing_key, payload=envelope.model_dump(mode="json"))
            )
        relay = relay_for(runs_database, bus)
        assert await relay.publish_batch() == 1
        assert await relay.publish_batch() == 0
        await cleanup(bus, spec)

    async def test_event_survives_a_broker_outage(
        self, runs_database: Database, bus: MessageBus, org_id: uuid.UUID
    ) -> None:
        """If publishing fails, the row stays unpublished and goes out on the next pass."""
        spec, routing_key = unique_spec()
        await bus.declare(spec)
        envelope = event(org_id)
        async with runs_database.sessions() as session, session.begin():
            session.add(
                Outbox(event_id=envelope.event_id, routing_key=routing_key, payload=envelope.model_dump(mode="json"))
            )

        class BrokenBus:
            async def publish_raw(self, *_: Any) -> None:
                raise ConnectionError("broker is down")

        with pytest.raises(ConnectionError):
            await OutboxRelay(runs_database, BrokenBus(), Outbox, service="test").publish_batch()  # type: ignore[arg-type]

        async with runs_database.sessions() as session:
            row = await session.scalar(select(Outbox).where(Outbox.event_id == envelope.event_id))
            assert row.published_at is None

        assert await relay_for(runs_database, bus).publish_batch() == 1
        assert len(await drain(bus, spec.name)) == 1
        await cleanup(bus, spec)

    async def test_two_relays_never_send_the_same_row(
        self, runs_database: Database, bus: MessageBus, org_id: uuid.UUID
    ) -> None:
        spec, routing_key = unique_spec()
        await bus.declare(spec)
        async with runs_database.sessions() as session, session.begin():
            for _ in range(50):
                envelope = event(org_id)
                session.add(
                    Outbox(
                        event_id=envelope.event_id, routing_key=routing_key, payload=envelope.model_dump(mode="json")
                    )
                )
        relays = [OutboxRelay(runs_database, bus, Outbox, service="test", batch_size=10) for _ in range(4)]
        published = 0
        while True:
            counts = await asyncio.gather(*(relay.publish_batch() for relay in relays))
            if not any(counts):
                break
            published += sum(counts)
        assert published == 50
        ids = await read_all(bus, spec.name)
        assert len(ids) == 50
        assert len(set(ids)) == 50, "a row was published twice"
        await cleanup(bus, spec)

    async def test_a_batch_is_published_concurrently(self, runs_database: Database, org_id: uuid.UUID) -> None:
        """Each publish waits for a broker confirm, so one at a time caps the whole system.

        At 41 runs per second the relay fell behind and 9,298 events piled up in the
        outbox: see docs/loadtest.md.
        """
        async with runs_database.sessions() as session, session.begin():
            for _ in range(20):
                envelope = event(org_id)
                session.add(
                    Outbox(event_id=envelope.event_id, routing_key="test", payload=envelope.model_dump(mode="json"))
                )

        class SlowConfirmBus:
            in_flight = 0
            peak = 0

            async def publish_raw(self, *_: Any) -> None:
                SlowConfirmBus.in_flight += 1
                SlowConfirmBus.peak = max(SlowConfirmBus.peak, SlowConfirmBus.in_flight)
                await asyncio.sleep(0.01)
                SlowConfirmBus.in_flight -= 1

        relay = OutboxRelay(runs_database, SlowConfirmBus(), Outbox, service="test")  # type: ignore[arg-type]
        assert await relay.publish_batch() == 20
        assert SlowConfirmBus.peak == 20

    async def test_one_failed_publish_keeps_the_rest_of_the_batch(
        self, runs_database: Database, org_id: uuid.UUID
    ) -> None:
        """Confirmed rows stay marked, so a partial failure resends only what failed."""
        envelopes = [event(org_id) for _ in range(5)]
        refused = envelopes[2].event_id
        async with runs_database.sessions() as session, session.begin():
            for envelope in envelopes:
                session.add(
                    Outbox(event_id=envelope.event_id, routing_key="test", payload=envelope.model_dump(mode="json"))
                )

        class RefusesOne:
            async def publish_raw(self, _key: str, _body: bytes, _headers: Any, message_id: str) -> None:
                if message_id == str(refused):
                    raise ConnectionError("the broker refused this message")

        relay = OutboxRelay(runs_database, RefusesOne(), Outbox, service="test")  # type: ignore[arg-type]
        with pytest.raises(ConnectionError):
            await relay.publish_batch()

        async with runs_database.sessions() as session:
            waiting = (await session.scalars(select(Outbox.event_id).where(Outbox.published_at.is_(None)))).all()
        assert waiting == [refused]


class TestConsumer:
    async def test_same_message_twice_has_one_effect(
        self, runs_database: Database, bus: MessageBus, org_id: uuid.UUID
    ) -> None:
        spec, routing_key = unique_spec()
        effects: list[uuid.UUID] = []
        seen = asyncio.Event()

        async def handler(envelope: EventEnvelope, _: Any) -> None:
            async with runs_database.sessions() as session, session.begin():
                if await first_delivery(session, ProcessedMessage, "test", envelope.event_id):
                    effects.append(envelope.event_id)
            seen.set()

        await bus.consume(spec, handler, prefetch=4)
        envelope = event(org_id)
        await publish(bus, routing_key, envelope)
        await publish(bus, routing_key, envelope)
        await asyncio.wait_for(seen.wait(), 5)
        await asyncio.sleep(0.5)
        assert effects == [envelope.event_id]
        await cleanup(bus, spec)

    async def test_failed_message_is_retried_then_succeeds(self, bus: MessageBus, org_id: uuid.UUID) -> None:
        spec, routing_key = unique_spec(retries=(1, 1))
        attempts = 0
        done = asyncio.Event()

        async def handler(_: EventEnvelope, __: Any) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("temporary failure")
            done.set()

        await bus.consume(spec, handler, prefetch=1)
        await publish(bus, routing_key, event(org_id))
        await asyncio.wait_for(done.wait(), 10)
        assert attempts == 3
        await cleanup(bus, spec)

    async def test_exhausted_message_lands_in_the_dead_letter_queue(self, bus: MessageBus, org_id: uuid.UUID) -> None:
        spec, routing_key = unique_spec(retries=(1,))
        attempts = 0

        async def handler(_: EventEnvelope, __: Any) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("permanent failure")

        await bus.consume(spec, handler, prefetch=1)
        envelope = event(org_id)
        await publish(bus, routing_key, envelope)

        dead = await drain(bus, spec.dlq, timeout=8)
        assert len(dead) == 1
        assert attempts == 2
        assert json.loads(dead[0].body)["event_id"] == str(envelope.event_id)
        assert "retries exhausted" in dead[0].headers["x-death-reason"]
        await cleanup(bus, spec)

    async def test_invalid_contract_skips_retries(self, bus: MessageBus) -> None:
        spec, routing_key = unique_spec(retries=(1, 1, 1))
        calls = 0

        async def handler(_: EventEnvelope, __: Any) -> None:
            nonlocal calls
            calls += 1

        await bus.consume(spec, handler, prefetch=1)
        await bus.publish_raw(routing_key, b'{"event_type": "run.requested"}', {}, str(uuid.uuid4()))

        dead = await drain(bus, spec.dlq, timeout=4)
        assert len(dead) == 1
        assert calls == 0
        assert "contract violation" in dead[0].headers["x-death-reason"]
        await cleanup(bus, spec)

    async def test_handler_can_declare_a_message_unprocessable(self, bus: MessageBus, org_id: uuid.UUID) -> None:
        spec, routing_key = unique_spec(retries=(1, 1, 1))
        calls = 0

        async def handler(_: EventEnvelope, __: Any) -> None:
            nonlocal calls
            calls += 1
            raise PoisonMessage("cannot ever work")

        await bus.consume(spec, handler, prefetch=1)
        await publish(bus, routing_key, event(org_id))
        assert len(await drain(bus, spec.dlq, timeout=4)) == 1
        assert calls == 1
        await cleanup(bus, spec)

    async def test_message_published_while_no_consumer_runs_is_delivered_later(
        self, bus: MessageBus, org_id: uuid.UUID
    ) -> None:
        spec, routing_key = unique_spec()
        await bus.declare(spec)
        envelope = event(org_id)
        await publish(bus, routing_key, envelope)

        received: list[uuid.UUID] = []
        done = asyncio.Event()

        async def handler(message: EventEnvelope, _: Any) -> None:
            received.append(message.event_id)
            done.set()

        await bus.consume(spec, handler, prefetch=1)
        await asyncio.wait_for(done.wait(), 5)
        assert received == [envelope.event_id]
        await cleanup(bus, spec)
