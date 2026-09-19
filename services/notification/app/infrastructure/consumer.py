"""Consuming side of the queue."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika

logger = logging.getLogger(__name__)


class MalformedEvent(Exception):
    """The envelope does not carry what is needed, and redelivering it would not help.
    """


class BrokerConsumer:
    """Subscription to the queue of the other contexts' events."""

    def __init__(
        self,
        *,
        url: str,
        exchange_name: str,
        queue_name: str,
        routing_keys: str,
        prefetch: int,
        on_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._queue_name = queue_name
        self._routing_keys = [k.strip() for k in routing_keys.split(",") if k.strip()]
        self._prefetch = prefetch
        self._on_event = on_event

        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None

    @property
    def queue_name(self) -> str:
        return self._queue_name

    @property
    def routing_keys(self) -> list[str]:
        return list(self._routing_keys)

    @property
    def is_connected(self) -> bool:
        """Whether the connection to the broker is usable."""
        if self._connection is None or self._connection.is_closed:
            return False

        connected_event = getattr(self._connection, "connected", None)
        return connected_event.is_set() if connected_event is not None else True

    async def start(self) -> None:
        """Declare the queue, bind it to the exchange and start consuming."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch)

        exchange = await self._channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )

        queue = await self._channel.declare_queue(self._queue_name, durable=True)
        for key in self._routing_keys:
            await queue.bind(exchange, routing_key=key)
            logger.info("Queue '%s' bound to pattern '%s'", self._queue_name, key)

        await queue.consume(self._handle)
        logger.info("In ascolto su '%s'", self._queue_name)

    async def close(self) -> None:
        """Close the connection."""
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()

    async def _handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        """Consume an event."""
        try:
            envelope = json.loads(message.body)
        except (ValueError, UnicodeDecodeError) as error:
            logger.error("Unreadable envelope, discarded: %s", error)
            await message.reject(requeue=False)
            return

        try:
            await self._on_event(envelope)
        except MalformedEvent as error:
            # A malformed event is not requeued.
            logger.error("Malformed event, discarded: %s", error)
            await message.reject(requeue=False)
            return
        except Exception:
            logger.exception("Event consumption failed, the message goes back to the queue")
            await message.reject(requeue=True)
            return

        await message.ack()
