"""RabbitMQ topology, publishing and consuming.

Delivery is at-least-once. Publishers use broker confirms, consumers ack only
after their handler commits, and handlers deduplicate on ``event_id``.

A failed message moves through tiered retry queues, each holding it for a fixed
delay before dead-lettering it back to the main queue. After the last tier it
lands in ``<queue>.dlq`` for inspection. Messages that can never succeed, such
as those that fail contract validation, skip the retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import ValidationError

from agentarena_common.context import correlation_id_var
from agentarena_common.events import EventEnvelope, UnknownEventError
from agentarena_common.metrics import EVENT_HANDLER_SECONDS, EVENTS_CONSUMED, EVENTS_PUBLISHED
from agentarena_common.resilience import backoff_delay
from agentarena_common.telemetry import extract_context, get_tracer

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

EVENTS_EXCHANGE = "agentarena.events"
RETRY_COUNT_HEADER = "x-retry-count"
PUBLISH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class QueueSpec:
    name: str
    bindings: tuple[str, ...]
    retry_delays_seconds: tuple[int, ...] = (1, 5, 30)
    extra_arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def dlq(self) -> str:
        return f"{self.name}.dlq"

    def retry_queue(self, attempt: int) -> str:
        return f"{self.name}.retry.{self.retry_delays_seconds[attempt - 1]}s"

    @property
    def arguments(self) -> dict[str, Any]:
        return {
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": self.dlq,
            **self.extra_arguments,
        }


SCHEDULER_QUEUE = QueueSpec(
    "orchestrator.scheduler",
    ("run.requested", "execution.started", "execution.completed", "execution.failed", "evaluation.completed"),
)
JOBS_QUEUE = QueueSpec("orchestrator.execution-jobs", ("execution.requested",))
EVALUATION_QUEUE = QueueSpec("evaluation.requests", ("evaluation.requested", "run.completed"))

ALL_QUEUES = (SCHEDULER_QUEUE, JOBS_QUEUE, EVALUATION_QUEUE)


class PoisonMessage(Exception):
    """The message can never be processed, so retrying would only waste time."""


Handler = Callable[[EventEnvelope, AbstractIncomingMessage], Awaitable[None]]


async def _settle(action: Callable[[], Awaitable[None]]) -> None:
    """Ack or nack a delivery. If its channel has gone the broker already took the
    message back and will redeliver it, and deduplication absorbs the repeat. So
    this is not a failure, and must not send a processed message down the retry path.
    """
    try:
        await action()
    except (aio_pika.exceptions.AMQPError, ConnectionError, RuntimeError) as exc:
        logger.warning("delivery not settled, the broker will redeliver", extra={"error": type(exc).__name__})


class MessageBus:
    def __init__(self, url: str, service: str) -> None:
        self.url = url
        self.service = service
        self.connection: AbstractRobustConnection | None = None
        self.channel: AbstractChannel | None = None
        self.exchange: AbstractExchange | None = None
        self._consumer_channels: list[AbstractChannel] = []
        self._consumers: list[tuple[AbstractQueue, str]] = []

    async def connect(self, *, attempts: int = 30) -> None:
        """Connect with backoff. Reconnection after this is automatic."""
        for attempt in range(1, attempts + 1):
            try:
                self.connection = await aio_pika.connect_robust(self.url, client_properties={"service": self.service})
                break
            except (OSError, aio_pika.exceptions.AMQPError) as exc:
                if attempt == attempts:
                    raise
                delay = backoff_delay(attempt, 0.5, 5.0)
                logger.warning("broker not ready, retrying", extra={"attempt": attempt, "error": str(exc)})
                await asyncio.sleep(delay)
        assert self.connection is not None
        self.channel = await self.connection.channel(publisher_confirms=True)
        self.exchange = await self.channel.declare_exchange(EVENTS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    @property
    def is_connected(self) -> bool:
        return self.connection is not None and not self.connection.is_closed

    async def declare(self, spec: QueueSpec) -> AbstractQueue:
        """Declare a queue with its retry tiers and DLQ. Safe to repeat."""
        assert self.channel is not None
        assert self.exchange is not None
        await self.channel.declare_queue(spec.dlq, durable=True, arguments={"x-queue-type": "quorum"})
        for attempt, delay in enumerate(spec.retry_delays_seconds, start=1):
            await self.channel.declare_queue(
                spec.retry_queue(attempt),
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": spec.name,
                },
            )
        queue = await self.channel.declare_queue(spec.name, durable=True, arguments=spec.arguments)
        for routing_key in spec.bindings:
            await queue.bind(self.exchange, routing_key)
        return queue

    async def publish_raw(self, routing_key: str, body: bytes, headers: dict[str, Any], message_id: str) -> None:
        """Publish and wait for the broker's confirm."""
        assert self.exchange is not None
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message_id,
            type=routing_key,
            headers=headers,
        )
        # The timeout stops an outbox transaction from waiting out a broker outage.
        await self.exchange.publish(message, routing_key=routing_key, mandatory=False, timeout=PUBLISH_TIMEOUT_SECONDS)
        EVENTS_PUBLISHED.labels(self.service, routing_key).inc()

    async def _send_to_queue(self, queue: str, message: AbstractIncomingMessage, headers: dict[str, Any]) -> None:
        assert self.channel is not None
        copy = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message.message_id,
            type=message.type,
            headers=headers,
        )
        await self.channel.default_exchange.publish(copy, routing_key=queue, timeout=PUBLISH_TIMEOUT_SECONDS)

    async def consume(
        self,
        spec: QueueSpec,
        handler: Handler,
        *,
        prefetch: int,
    ) -> None:
        """Start consuming on a dedicated channel so prefetch applies per queue."""
        assert self.connection is not None
        channel = await self.connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        self._consumer_channels.append(channel)
        await self.declare(spec)
        # Declared on this channel, not fetched: a robust channel only restores
        # queues it declared itself, and the consumer goes with them. Without this,
        # the connection comes back after a broker restart but nothing consumes.
        queue = await channel.declare_queue(spec.name, durable=True, arguments=spec.arguments)

        async def on_message(message: AbstractIncomingMessage) -> None:
            await self._handle(spec, handler, message)

        tag = await queue.consume(on_message)
        self._consumers.append((queue, tag))
        logger.info("consumer started", extra={"queue": spec.name, "prefetch": prefetch})

    async def _handle(self, spec: QueueSpec, handler: Handler, message: AbstractIncomingMessage) -> None:
        headers = dict(message.headers or {})
        event_type = message.type or "unknown"
        started = time.perf_counter()
        context = extract_context(headers)
        with tracer.start_as_current_span(
            f"{spec.name} process",
            context=context,
            kind=SpanKind.CONSUMER,
            attributes={
                "messaging.system": "rabbitmq",
                "messaging.destination.name": spec.name,
                "messaging.message.id": message.message_id or "",
                "messaging.operation.type": "process",
                "event.type": event_type,
            },
        ) as span:
            token = None
            try:
                envelope = self._parse(message)
                token = correlation_id_var.set(envelope.correlation_id)
                span.set_attribute("correlation.id", envelope.correlation_id)
                await handler(envelope, message)
            except PoisonMessage as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                await self._dead_letter(spec, message, headers, event_type, str(exc))
            except Exception as exc:  # noqa: BLE001 - every failure takes the retry path
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                await self._retry_or_dead_letter(spec, message, headers, event_type, exc)
            else:
                await _settle(message.ack)
                EVENTS_CONSUMED.labels(self.service, spec.name, event_type, "processed").inc()
            finally:
                if token is not None:
                    correlation_id_var.reset(token)
                EVENT_HANDLER_SECONDS.labels(self.service, spec.name).observe(time.perf_counter() - started)

    @staticmethod
    def _parse(message: AbstractIncomingMessage) -> EventEnvelope:
        try:
            envelope = EventEnvelope.model_validate(json.loads(message.body))
            envelope.payload()
        except (ValueError, ValidationError, UnknownEventError) as exc:
            raise PoisonMessage(f"contract violation: {exc}") from exc
        return envelope

    async def _retry_or_dead_letter(
        self,
        spec: QueueSpec,
        message: AbstractIncomingMessage,
        headers: dict[str, Any],
        event_type: str,
        error: Exception,
    ) -> None:
        attempt = int(headers.get(RETRY_COUNT_HEADER, 0)) + 1
        if attempt > len(spec.retry_delays_seconds):
            await self._dead_letter(spec, message, headers, event_type, f"retries exhausted: {error}")
            return
        queue = spec.retry_queue(attempt)
        logger.warning(
            "handler failed, scheduling retry",
            extra={"queue": spec.name, "retry_queue": queue, "attempt": attempt, "error": str(error)},
        )
        try:
            await self._send_to_queue(queue, message, {**headers, RETRY_COUNT_HEADER: attempt})
        except Exception:  # noqa: BLE001 - the broker keeps the message if this fails
            logger.exception("could not schedule retry, requeueing", extra={"queue": spec.name})
            await _settle(lambda: message.nack(requeue=True))
            return
        await _settle(message.ack)
        EVENTS_CONSUMED.labels(self.service, spec.name, event_type, "retried").inc()

    async def _dead_letter(
        self,
        spec: QueueSpec,
        message: AbstractIncomingMessage,
        headers: dict[str, Any],
        event_type: str,
        reason: str,
    ) -> None:
        logger.error("message dead-lettered", extra={"queue": spec.name, "reason": reason, "event_type": event_type})
        try:
            await self._send_to_queue(spec.dlq, message, {**headers, "x-death-reason": reason[:500]})
        except Exception:  # noqa: BLE001 - fall back to the broker's own dead-lettering
            logger.exception("could not publish to dlq, rejecting", extra={"queue": spec.name})
            await _settle(lambda: message.reject(requeue=False))
            return
        await _settle(message.ack)
        EVENTS_CONSUMED.labels(self.service, spec.name, event_type, "dead_lettered").inc()

    async def stop_consuming(self) -> None:
        """Stop taking new messages but leave channels open for in-flight acks."""
        for queue, tag in self._consumers:
            try:
                await queue.cancel(tag)
            except Exception:  # noqa: BLE001 - shutdown must continue
                logger.warning("could not cancel consumer", extra={"queue": queue.name})
        self._consumers.clear()

    async def close(self) -> None:
        await self.stop_consuming()
        if self.connection is not None:
            await self.connection.close()
