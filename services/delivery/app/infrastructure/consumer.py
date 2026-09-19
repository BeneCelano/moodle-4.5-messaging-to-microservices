"""Consuming side of the queue."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika

from ..application.deliver import Assignment, MalformedEvent

logger = logging.getLogger(__name__)


class BrokerConsumer:
    """Subscription to the gateway queues."""

    def __init__(
        self,
        *,
        url: str,
        exchange_name: str,
        queue_prefix: str,
        routing_keys: str,
        prefetch: int,
        on_event: Callable[[dict], Awaitable[list[Assignment]]],
        on_assignment: Callable[[Assignment], Awaitable[float | None]],
    ) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._prefix = queue_prefix
        self._routing_keys = [k.strip() for k in routing_keys.split(",") if k.strip()]
        self._prefetch = prefetch
        self._on_event = on_event
        self._on_assignment = on_assignment

        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None

    @property
    def events_queue(self) -> str:
        """Name of the domain events queue."""
        return f"{self._prefix}.events"

    @property
    def work_queue(self) -> str:
        """Name of the delivery assignments queue."""
        return f"{self._prefix}.work"

    @property
    def retry_queue(self) -> str:
        """Name of the wait queue between attempts."""
        return f"{self._prefix}.retry"

    @property
    def is_connected(self) -> bool:
        """Whether the connection to the broker is usable."""
        return self._connection is not None and not self._connection.is_closed

    async def start(self) -> None:
        """Declare the queues, bind them to the exchange and start consuming."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch)

        exchange = await self._channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )

        events = await self._channel.declare_queue(self.events_queue, durable=True)
        for key in self._routing_keys:
            await events.bind(exchange, routing_key=key)
            logger.info("Queue '%s' bound to pattern '%s'", self.events_queue, key)

        work = await self._channel.declare_queue(self.work_queue, durable=True)

        # At expiry the message returns to the work queue (the empty exchange routes by
        # queue name).
        await self._channel.declare_queue(
            self.retry_queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": self.work_queue,
            },
        )

        await events.consume(self._handle_event)
        await work.consume(self._handle_assignment)
        logger.info("In ascolto su '%s' e '%s'", self.events_queue, self.work_queue)

    async def close(self) -> None:
        """Close the connection."""
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()

    # -- handlers ------------------------------------------------------------------------------

    async def _handle_event(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        """Accept a domain event and emit one assignment per delivery."""
        try:
            envelope = json.loads(message.body)
        except (ValueError, UnicodeDecodeError) as error:
            logger.error("Unreadable envelope, discarded: %s", error)
            await message.reject(requeue=False)
            return

        try:
            assignments = await self._on_event(envelope)
        except MalformedEvent as error:
            # A malformed event is not requeued.
            logger.error("Malformed event, discarded: %s", error)
            await message.reject(requeue=False)
            return
        except Exception:
            # A fault of ours or of the database: requeued.
            logger.exception("Event acceptance failed, the message goes back to the queue")
            await message.reject(requeue=True)
            return

        for assignment in assignments:
            await self._publish_assignment(assignment, delay_seconds=None)

        await message.ack()

    async def _handle_assignment(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        """Perform a delivery assignment."""
        try:
            data = json.loads(message.body)
            assignment = Assignment(
                delivery_id=data["delivery_id"],
                event_id=data["event_id"],
                recipient_id=data["recipient_id"],
                channel=data["channel"],
            )
        except (ValueError, KeyError, UnicodeDecodeError) as error:
            logger.error("Incarico illeggibile, scartato: %s", error)
            await message.reject(requeue=False)
            return

        try:
            wait_seconds = await self._on_assignment(assignment)
        except Exception:
            logger.exception("Assignment execution failed, the message goes back to the queue")
            await message.reject(requeue=True)
            return

        # The assignment is always acknowledged: a retry is a new message in the wait
        # queue.
        if wait_seconds is not None:
            await self._publish_assignment(assignment, delay_seconds=wait_seconds)
        await message.ack()

    async def _publish_assignment(self, assignment: Assignment, delay_seconds: float | None) -> None:
        """Put an assignment in the work queue, or in the wait queue."""
        if self._channel is None:
            raise RuntimeError("The consumer is not connected to the broker.")

        body = json.dumps(
            {
                "delivery_id": assignment.delivery_id,
                "event_id": assignment.event_id,
                "recipient_id": assignment.recipient_id,
                "channel": assignment.channel,
            }
        ).encode()

        queued = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            # The expiry is in milliseconds and as a string, the form the broker
            # accepts.
            expiration=None if delay_seconds is None else delay_seconds,
        )

        destination = self.work_queue if delay_seconds is None else self.retry_queue
        await self._channel.default_exchange.publish(queued, routing_key=destination)
