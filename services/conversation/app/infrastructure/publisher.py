"""Adapter towards RabbitMQ."""

from __future__ import annotations

import json
import logging

import aio_pika

logger = logging.getLogger(__name__)


class RabbitMQPublisher:
    """Publish the event envelopes on the configured exchange."""

    def __init__(self, url: str, exchange_name: str) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self) -> None:
        """Open the connection and declare the exchange."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel(publisher_confirms=True)
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )
        logger.info("Connected to the broker, exchange '%s' declared", self._exchange_name)

    async def close(self) -> None:
        """Close the connection."""
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()

    @property
    def is_connected(self) -> bool:
        """Whether the connection to the broker is usable now."""
        if self._connection is None or self._connection.is_closed:
            return False

        connected_event = getattr(self._connection, "connected", None)
        return connected_event.is_set() if connected_event is not None else True

    async def publish(self, routing_key: str, envelope: dict, message_id: str) -> None:
        """Publish an envelope, waiting for the broker confirmation."""
        if self._exchange is None:
            raise RuntimeError("The publisher is not connected to the broker.")

        message = aio_pika.Message(
            body=json.dumps(envelope, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message_id,
            correlation_id=envelope.get("correlation_id"),
            type=envelope.get("event_type"),
            headers={"event_version": envelope.get("event_version", 1)},
        )

        await self._exchange.publish(message, routing_key=routing_key)
