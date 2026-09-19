"""Full-chain smoke test of the delivery, on real infrastructure.

    python scripts/smoke.py                            the full chain, nine checks
    python scripts/smoke.py --retry-test               no delivery lost: retries and dead letter
    python scripts/smoke.py --boundary-test            the monolith schema stays unreachable
    python scripts/smoke.py --notification-event-test  the gateway accepts a notification.created event
    python scripts/smoke.py --publish                  publish an event and print its id
    python scripts/smoke.py --wait <id>                wait until that id has been delivered

The environment must carry DG_DATABASE_URL and DG_RABBITMQ_URL. The last printed line is the one
`verify.sh` reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime

import aio_pika
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

EVENT = "conversation.message.sent"
NOTIFICATION_EVENT = "notification.created"
NOTIFICATION_TYPE = "messagecontactrequests"
COMPONENT = "moodle"
PROVIDER = "instantmessage"
INDIVIDUAL_CONVERSATION = 1

# How long to wait for the gateway to consume, resolve the channels and deliver.
EVENT_WAIT = 20.0
DELIVERY_WAIT = 30.0
MOCK_WAIT = 10.0

# How long to wait before concluding that the republication produced nothing.
IDEMPOTENCY_WAIT = 8.0

# How long to wait for a repeatedly refused delivery to exhaust its attempts (2, 4, 8,
# 16 s).
DEAD_LETTER_WAIT = 150.0

# How long to wait, with the service down, before concluding that nobody consumed.
SILENCE_WAIT = 15.0


class Interrupted(Exception):
    """A prerequisite check failed; the following checks are skipped."""


class Outcome:
    """Register of the checks; each one is printed as it happens."""

    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, passed: bool, name: str, detail: str = "") -> bool:
        """Record a check and print it immediately."""
        self.rows.append((passed, name, detail))
        print(f"{'OK    ' if passed else 'FAILED'}  {name:<52}  {detail}", flush=True)
        return passed

    @property
    def passed_count(self) -> int:
        return sum(1 for ok, _, _ in self.rows if ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for ok, _, _ in self.rows if not ok)

    def summary(self) -> None:
        """The final line, the one verify.sh reads."""
        print()
        print(f"RESULT {len(self.rows)} checks, {self.passed_count} passed, {self.failed_count} failed")


async def test_recipient(engine) -> tuple[int, bool]:
    """A real recipient from the views, preferring one with a registered device (without it
    `airnotifier` is not chosen).
    """
    async with engine.connect() as conn:
        with_device = await conn.execute(
            text(
                "SELECT r.user_id FROM recipient_view r"
                " JOIN device_view d ON d.user_id = r.user_id"
                " WHERE r.deleted = 0 AND r.suspended = 0 AND r.auth_method <> 'nologin'"
                "   AND r.email IS NOT NULL AND r.email <> '' AND r.email_stop = 0"
                "   AND d.enabled = 1"
                " ORDER BY r.user_id LIMIT 1"
            )
        )
        row = with_device.first()
        if row is not None:
            return int(row.user_id), True

        without = await conn.execute(
            text(
                "SELECT user_id FROM recipient_view"
                " WHERE deleted = 0 AND suspended = 0 AND auth_method <> 'nologin'"
                "   AND email IS NOT NULL AND email <> '' AND email_stop = 0"
                " ORDER BY user_id LIMIT 1"
            )
        )
        row = without.first()

    if row is None:
        raise SystemExit(
            "No deliverable recipient in the view: the test cannot run.\n"
            "At least one user is needed who is not deleted, not suspended, and has an email address."
        )
    return int(row.user_id), False


def test_envelope(event_id: str, recipient: int, marker: str) -> dict:
    """Compose the envelope exactly as the ConversationService publishes it: `source_component` and
    `source_event_name` are what the gateway derives the site settings pair from.
    """
    now = int(time.time())
    return {
        "event_id": event_id,
        "event_type": EVENT,
        "event_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "smoke-delivery",
        "correlation_id": uuid.uuid4().hex[:16],
        "payload": {
            "message_id": now,
            "conversation_id": now,
            "conversation_type": INDIVIDUAL_CONVERSATION,
            "user_id_from": recipient,
            "recipient_ids": [recipient],
            "text": f"Delivery chain smoke test {marker}",
            "message_format": 0,
            "time_created": now,
            "source_component": COMPONENT,
            "source_event_name": PROVIDER,
        },
    }


async def wait_for_event(engine, event_id: str) -> bool:
    """Wait until the gateway records the received event."""
    deadline = time.monotonic() + EVENT_WAIT
    while time.monotonic() < deadline:
        async with engine.connect() as conn:
            found = await conn.execute(
                text("SELECT event_id FROM event WHERE event_id = :e"), {"e": event_id}
            )
            if found.first() is not None:
                return True
        await asyncio.sleep(0.5)
    return False


async def wait_for_deliveries(engine, event_id: str, count: int) -> list:
    """Wait until the expected deliveries reach a final status."""
    deadline = time.monotonic() + DELIVERY_WAIT
    last_rows: list = []
    while time.monotonic() < deadline:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT channel, status, attempts, last_error FROM delivery"
                    " WHERE event_id = :e ORDER BY channel"
                ),
                {"e": event_id},
            )
            last_rows = list(rows)
        if len(last_rows) >= count and all(r.status != "pending" for r in last_rows):
            return last_rows
        await asyncio.sleep(0.5)
    return last_rows


async def ask_mock(mock: str) -> dict | None:
    """Query the delivery mock."""
    try:
        async with httpx.AsyncClient(timeout=MOCK_WAIT) as client:
            response = await client.get(f"{mock.rstrip('/')}/events")
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


async def run(args: argparse.Namespace) -> int:
    """Walk the chain and report the outcome of each check."""
    outcome = Outcome()

    database_url = os.environ.get("DG_DATABASE_URL")
    rabbitmq_url = os.environ.get("DG_RABBITMQ_URL")
    exchange_name = os.environ.get("DG_RABBITMQ_EXCHANGE", "moodle.messaging")

    if not database_url or not rabbitmq_url:
        raise SystemExit("DG_DATABASE_URL and DG_RABBITMQ_URL must be set in the environment.")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    connection = None
    event_id = str(uuid.uuid4())
    marker = event_id[:8]

    try:
        # 1. The two dependencies of the gateway and the processor register.
        fault = ""
        processors: list[str] = []
        try:
            async with engine.connect() as conn:
                active = await conn.execute(
                    text("SELECT name FROM processor WHERE enabled = 1 ORDER BY name")
                )
                processors = [r.name for r in active]
        except Exception as error:
            fault = str(error) or error.__class__.__name__

        try:
            connection = await asyncio.wait_for(
                aio_pika.connect_robust(rabbitmq_url), timeout=MOCK_WAIT
            )
        except Exception as error:
            fault = f"{fault}; broker: {error}" if fault else f"broker: {error}"

        if not outcome.check(
            not fault and bool(processors) and connection is not None,
            "The gateway reaches the database and the broker",
            fault or f"active processors: {', '.join(processors) or 'NONE'}",
        ):
            raise Interrupted

        # 2. The mocks must not be in fault: an unmet prerequisite stops the test here.
        status = await ask_mock(args.mock)
        if status is not None and status.get("failing"):
            outcome.check(
                False,
                "The delivery providers are not in failure mode",
                f"switch is on: curl -X POST {args.mock.rstrip('/')}/repair",
            )
            raise Interrupted

        # 3. A real recipient, chosen from the views on the monolith.
        recipient, with_device = await test_recipient(engine)
        expected = ["email"]
        if with_device and "airnotifier" in processors:
            expected.append("airnotifier")
        outcome.check(
            True,
            "Real recipient read from the views",
            f"user {recipient}, expected channels: {', '.join(sorted(expected))}",
        )

        # 4. The publication, on the exchange as the producer declares it.
        channel = await connection.channel(publisher_confirms=True)
        exchange_obj = await channel.declare_exchange(
            exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )
        envelope = test_envelope(event_id, recipient, marker)
        await exchange_obj.publish(
            aio_pika.Message(
                body=json.dumps(envelope, ensure_ascii=False).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=event_id,
                type=EVENT,
            ),
            routing_key=EVENT,
        )
        outcome.check(True, "Event published on the exchange", f"key {EVENT}, id {marker}")

        # 5. The arrival.
        if not outcome.check(
            await wait_for_event(engine, event_id),
            "The gateway receives and records the event",
            f"waited up to {EVENT_WAIT:.0f}s",
        ):
            raise Interrupted

        # 6 and 7. The assignments and the chosen channels.
        rows = await wait_for_deliveries(engine, event_id, len(expected))
        found_rows = sorted(r.channel for r in rows)
        outcome.check(
            len(rows) == len(expected),
            "One delivery per resolved channel",
            f"{len(rows)} rows, expected {len(expected)}",
        )
        outcome.check(
            found_rows == sorted(expected),
            "The chosen channels are the expected ones",
            f"chosen: {', '.join(found_rows) or 'none'}",
        )

        # 8. The outcome of the delivery.
        completed = [r for r in rows if r.status == "delivered"]
        errors = "; ".join(f"{r.channel}: {r.last_error}" for r in rows if r.last_error)
        outcome.check(
            bool(rows) and len(completed) == len(rows),
            "Every delivery is completed",
            errors or f"{len(completed)} of {len(rows)}, attempts "
            + ", ".join(f"{r.channel}={r.attempts}" for r in rows),
        )

        # 9. What reached the mocks; if the mock does not answer, the check is declared not run.
        received = await ask_mock(args.mock)
        if received is None:
            outcome.check(
                True,
                "The delivery providers received it",
                f"NOT VERIFIED: {args.mock} does not answer",
            )
        else:
            mail = any(marker in (m.get("text") or "") for m in received.get("messages", []))
            pushes = any(
                marker in (n.get("alert") or "") for n in received.get("notifications", [])
            )
            arrived = {"email": mail, "airnotifier": pushes}
            missing = [c for c in expected if not arrived[c]]
            outcome.check(
                not missing,
                "The delivery providers received it",
                f"missing: {', '.join(missing)}" if missing else ", ".join(sorted(expected)),
            )

        # 10. Idempotency of the receiver: a redelivery must not produce a second delivery.
        await exchange_obj.publish(
            aio_pika.Message(
                body=json.dumps(envelope, ensure_ascii=False).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=event_id,
                type=EVENT,
            ),
            routing_key=EVENT,
        )
        await asyncio.sleep(IDEMPOTENCY_WAIT)
        async with engine.connect() as conn:
            after = await conn.execute(
                text("SELECT COUNT(*) AS quante FROM delivery WHERE event_id = :e"),
                {"e": event_id},
            )
            count = int(after.scalar() or 0)
        outcome.check(
            count == len(rows),
            "Redelivery does not produce a second delivery",
            f"{count} rows after republishing, there were {len(rows)}",
        )

    except Interrupted:
        # Already reported by the check that interrupted.
        pass

    finally:
        if not args.keep:
            try:
                async with engine.begin() as conn:
                    for table in ("delivery", "dead_letter", "event"):
                        await conn.execute(
                            text(f"DELETE FROM {table} WHERE event_id = :e"), {"e": event_id}
                        )
                print(f"\nCleanup: rows of event {marker} removed.")
                print("What reached the mock providers stays: it is the proof that delivery")
                print("happened, and it is cleared from the button on their page.")
            except Exception as error:
                print(f"\nCleanup failed, rows of event {marker} left behind: {error}")
        else:
            print(f"\nKept on request: the rows of event {marker}.")
        if connection is not None:
            await connection.close()
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------
# Parts shared by the modes
# ---------------------------------------------------------------------------------------------------


def environment() -> tuple[str, str, str]:
    """The three coordinates the service receives, read from the same environment."""
    database_url = os.environ.get("DG_DATABASE_URL")
    rabbitmq_url = os.environ.get("DG_RABBITMQ_URL")
    if not database_url or not rabbitmq_url:
        raise SystemExit("DG_DATABASE_URL and DG_RABBITMQ_URL must be set in the environment.")
    return database_url, rabbitmq_url, os.environ.get("DG_RABBITMQ_EXCHANGE", "moodle.messaging")


async def open_exchange(connection, exchange_name: str):
    """Declare the exchange with the same parameters as the producer: a different declaration would be
    refused by the broker, better here than in production.
    """
    channel = await connection.channel(publisher_confirms=True)
    return await channel.declare_exchange(exchange_name, aio_pika.ExchangeType.TOPIC, durable=True)


async def publish(exchange_obj, envelope: dict, event_id: str) -> None:
    """Publish an envelope with the key and properties the ConversationService uses."""
    await exchange_obj.publish(
        aio_pika.Message(
            body=json.dumps(envelope, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=event_id,
            type=EVENT,
        ),
        routing_key=EVENT,
    )


async def switch(mock: str, action: str) -> bool:
    """Switch the mocks' fault on or off."""
    try:
        async with httpx.AsyncClient(timeout=MOCK_WAIT) as client:
            response = await client.post(f"{mock.rstrip('/')}/{action}")
            response.raise_for_status()
            return True
    except Exception:
        return False


async def cleanup(engine, event_id: str) -> None:
    """Remove the rows produced by a test."""
    async with engine.begin() as conn:
        for table in ("delivery", "dead_letter", "event"):
            await conn.execute(text(f"DELETE FROM {table} WHERE event_id = :e"), {"e": event_id})


# ---------------------------------------------------------------------------------------------------
# Criterion (a): no delivery lost
# ---------------------------------------------------------------------------------------------------


async def retry_test(args: argparse.Namespace) -> int:
    """Check that a refused delivery is retried and, once exhausted, does not vanish."""
    outcome = Outcome()
    database_url, rabbitmq_url, exchange_name = environment()
    maximum = int(os.environ.get("DG_MAX_ATTEMPTS", "5"))
    base = float(os.environ.get("DG_RETRY_BASE_SECONDS", "2.0"))
    cap = float(os.environ.get("DG_RETRY_MAX_SECONDS", "60.0"))
    expected_wait = sum(min(base * (2 ** (t - 1)), cap) for t in range(1, maximum))

    engine = create_async_engine(database_url, pool_pre_ping=True)
    connection = None
    event_id = str(uuid.uuid4())
    marker = event_id[:8]
    fault_on = False
    recipient = 0

    try:
        connection = await asyncio.wait_for(
            aio_pika.connect_robust(rabbitmq_url), timeout=MOCK_WAIT
        )
        exchange_obj = await open_exchange(connection, exchange_name)

        fault_on = await switch(args.mock, "fail")
        if not outcome.check(
            fault_on,
            "The delivery providers refuse on command",
            f"{args.mock} in failure mode" if fault_on else "switch unreachable",
        ):
            raise Interrupted

        recipient, _ = await test_recipient(engine)
        envelope = test_envelope(event_id, recipient, marker)
        start = time.monotonic()
        await publish(exchange_obj, envelope, event_id)
        outcome.check(
            True, "Event published while delivery is impossible", f"id {marker}"
        )

        # The attempts exhaust by themselves: the final status is awaited, collecting
        # the distinct values of `attempts`.
        seen: list[int] = []
        rows: list = []
        deadline = time.monotonic() + DEAD_LETTER_WAIT
        while time.monotonic() < deadline:
            async with engine.connect() as conn:
                read_rows = await conn.execute(
                    text(
                        "SELECT channel, status, attempts, last_error FROM delivery"
                        " WHERE event_id = :e ORDER BY channel"
                    ),
                    {"e": event_id},
                )
                rows = list(read_rows)
            for row in rows:
                if row.attempts not in seen:
                    seen.append(row.attempts)
            if rows and all(r.status != "pending" for r in rows):
                break
            await asyncio.sleep(1.0)
        duration = time.monotonic() - start

        outcome.check(
            bool(rows) and max(seen or [0]) >= 2,
            "Delivery is retried, not abandoned at once",
            f"attempts observed: {sorted(seen) or 'none'}",
        )
        outcome.check(
            bool(rows) and all(r.attempts == maximum for r in rows),
            f"Attempts stop at the configured maximum ({maximum})",
            ", ".join(f"{r.channel}={r.attempts}" for r in rows) or "no row",
        )
        outcome.check(
            duration >= expected_wait,
            "The wait between attempts grew",
            f"{duration:.0f}s elapsed, at least {expected_wait:.0f}s from the expected progression",
        )
        outcome.check(
            bool(rows) and all(r.status == "dead" for r in rows),
            "The exhausted delivery is declared so",
            ", ".join(f"{r.channel}={r.status}" for r in rows) or "no row",
        )

        # The dead letter row.
        async with engine.connect() as conn:
            dead = list(
                await conn.execute(
                    text(
                        "SELECT channel, reason, attempts, envelope FROM dead_letter"
                        " WHERE event_id = :e ORDER BY channel"
                    ),
                    {"e": event_id},
                )
            )
        outcome.check(
            bool(dead) and len(dead) == len(rows),
            "Nothing is lost: every exhausted delivery becomes a dead letter",
            f"{len(dead)} dead letters, {len(rows)} deliveries",
        )

        # The kept envelope must be whole.
        whole = False
        if dead:
            kept = dead[0].envelope
            if isinstance(kept, str):
                kept = json.loads(kept)
            whole = (kept or {}).get("payload", {}).get("recipient_ids") == [recipient]
        outcome.check(
            whole,
            "The dead letter keeps the whole envelope",
            f"reason: {dead[0].reason[:56]}" if dead else "no dead letter",
        )

    except Interrupted:
        pass

    finally:
        # The repair is attempted anyway, before the cleanup.
        if fault_on:
            repaired = await switch(args.mock, "repair")
            print("\nMock providers " + ("repaired." if repaired else "NOT REPAIRED: intervene by hand."))
        if not args.keep:
            try:
                await cleanup(engine, event_id)
                print(f"Cleanup: rows of event {marker} removed.")
            except Exception as error:
                print(f"Cleanup failed, rows of {marker} left behind: {error}")
        if connection is not None:
            await connection.close()
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------
# Criterion (c): continuity with the service down
# ---------------------------------------------------------------------------------------------------


async def publish_only(args: argparse.Namespace) -> int:
    """Publish an event with the service down and check that nobody consumes it (first half of the
    continuity criterion).
    """
    outcome = Outcome()
    database_url, rabbitmq_url, exchange_name = environment()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    connection = None
    event_id = str(uuid.uuid4())
    recipient = 0
    published = False

    try:
        recipient, _ = await test_recipient(engine)
        connection = await asyncio.wait_for(
            aio_pika.connect_robust(rabbitmq_url), timeout=MOCK_WAIT
        )
        exchange_obj = await open_exchange(connection, exchange_name)
        await publish(exchange_obj, test_envelope(event_id, recipient, event_id[:8]), event_id)
        published = True
        outcome.check(
            True, "Event published with the service stopped", f"id {event_id[:8]}, user {recipient}"
        )

        await asyncio.sleep(SILENCE_WAIT)
        async with engine.connect() as conn:
            consumed = (
                await conn.execute(
                    text("SELECT event_id FROM event WHERE event_id = :e"), {"e": event_id}
                )
            ).first() is not None
        outcome.check(
            not consumed,
            "Nobody consumes it: the service is really stopped",
            f"waited {SILENCE_WAIT:.0f}s"
            if not consumed
            else "the event is already recorded, so the gateway was not stopped",
        )
    finally:
        if connection is not None:
            await connection.close()
        await engine.dispose()

    # The line verify.sh reads for the second half; printed even on a failed check.
    if published:
        print(f"EVENT {event_id}")

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


async def boundary_test(args: argparse.Namespace) -> int:
    """Check that the service cannot read the monolith database (criterion d)."""
    outcome = Outcome()
    database_url, _, _ = environment()
    engine = create_async_engine(database_url, pool_pre_ping=True)

    try:
        refused = False
        reason = ""
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT id FROM moodle_db.mdl_user LIMIT 1"))
        except Exception as error:
            refused = True
            reason = str(error).split("\n")[0][:70]
        outcome.check(
            refused,
            "The service cannot read the monolith tables",
            reason if refused else "THE READ SUCCEEDED: the boundary is not closed",
        )

        for view in ("recipient_view", "channel_preference_view", "site_default_view", "device_view"):
            count = -1
            view_error = ""
            try:
                async with engine.connect() as conn:
                    count = int(
                        (await conn.execute(text(f"SELECT COUNT(*) FROM {view}"))).scalar() or 0
                    )
            except Exception as error:
                view_error = str(error).split("\n")[0][:70]
            outcome.check(
                count >= 0,
                f"The {view} view answers",
                view_error or f"{count} rows",
            )
    finally:
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


async def wait_only(args: argparse.Namespace) -> int:
    """Wait until the given event is recorded and delivered (second half of the
    continuity criterion).
    """
    outcome = Outcome()
    database_url, _, _ = environment()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    event_id = args.wait_for

    try:
        recorded = await wait_for_event(engine, event_id)
        outcome.check(
            recorded,
            "The event published with the service stopped is consumed",
            f"id {event_id[:8]}, waited up to {EVENT_WAIT:.0f}s",
        )
        rows = await wait_for_deliveries(engine, event_id, 1) if recorded else []
        outcome.check(
            bool(rows) and all(r.status == "delivered" for r in rows),
            "And the delivery is completed",
            ", ".join(f"{r.channel}={r.status}" for r in rows) or "no delivery",
        )
    finally:
        if not args.keep:
            try:
                await cleanup(engine, event_id)
                print(f"\nCleanup: rows of event {event_id[:8]} removed.")
            except Exception as error:
                print(f"\nCleanup failed: {error}")
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------
# The new producer
# ---------------------------------------------------------------------------------------------------


async def notification_event_test(args: argparse.Namespace) -> int:
    """Check that the gateway delivers what the NotificationService publishes too."""
    database_url, rabbitmq_url, exchange = environment()
    outcome = Outcome()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    event_id = str(uuid.uuid4())
    marker = uuid.uuid4().hex[:12]

    print(f"New producer test, event {event_id}\n", flush=True)

    keys = os.environ.get("DG_RABBITMQ_ROUTING_KEYS", "")
    outcome.check(
        NOTIFICATION_EVENT in keys,
        "the queue is bound to the notification key",
        keys or "DG_RABBITMQ_ROUTING_KEYS not defined",
    )

    try:
        recipient, with_device = await test_recipient(engine)
        outcome.check(
            True,
            "recipient chosen from the views",
            f"user {recipient}" + (" with a device" if with_device else ""),
        )

        now = int(time.time())
        envelope = {
            "event_id": event_id,
            "event_type": NOTIFICATION_EVENT,
            "event_version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
            "producer": "notification-service",
            "correlation_id": uuid.uuid4().hex[:16],
            # The generic field names, as the NotificationService uses them.
            "payload": {
                "notification_id": now,
                "recipient_ids": [recipient],
                "user_id_from": recipient,
                "component": COMPONENT,
                "provider": NOTIFICATION_TYPE,
                "subject": f"Contact request {marker}",
                "full_message": f"New producer test {marker}",
                "full_message_format": 1,
                "full_message_html": f"<p>New producer test {marker}</p>",
                "small_message": "",
                "context_url": None,
                "context_url_name": None,
                "time_created": now,
            },
        }

        connection = await aio_pika.connect_robust(rabbitmq_url)
        try:
            exchange_obj = await open_exchange(connection, exchange)
            await exchange_obj.publish(
                aio_pika.Message(
                    body=json.dumps(envelope, ensure_ascii=False).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=event_id,
                    type=NOTIFICATION_EVENT,
                ),
                routing_key=NOTIFICATION_EVENT,
            )
            outcome.check(True, "the notification event was published")
        finally:
            await connection.close()

        received = await wait_for_event(engine, event_id)
        outcome.check(
            received,
            "the gateway received the new producer's event",
            "" if received else f"no trace within {EVENT_WAIT:.0f}s",
        )
        if not received:
            raise Interrupted

        deliveries = await wait_for_deliveries(engine, event_id, 1)
        outcome.check(
            bool(deliveries),
            "deliveries were born from it",
            ", ".join(f"{r.channel}:{r.status}" for r in deliveries) or "none",
        )
        outcome.check(
            bool(deliveries) and all(r.status == "delivered" for r in deliveries),
            "all deliveries succeeded",
            ", ".join(f"{r.channel}:{r.last_error or ''}" for r in deliveries if r.status != "delivered"),
        )

    except Interrupted:
        print("\nThe test stopped: the following checks would say nothing.")
    finally:
        await cleanup(engine, event_id)
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


def main() -> int:
    """Parse the arguments and run."""
    parser = argparse.ArgumentParser(description="Full-chain smoke test of the DeliveryGateway.")
    parser.add_argument(
        "--mock",
        "--simulacro",
        default=os.environ.get("DG_AIRNOTIFIER_URL", "http://192.168.72.74:8081"),
        help=(
            "Address of the delivery mocks. Defaults to DG_AIRNOTIFIER_URL, the same value the"
            " service receives from its configuration."
        ),
    )
    parser.add_argument(
        "--keep",
        "--conserva",
        action="store_true",
        help="Keep the rows produced by the test.",
    )

    # The modes are mutually exclusive.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--retry-test",
        "--prova-ritentativi",
        action="store_true",
        help="Criterion (a): puts the mocks in fault and checks that the delivery is retried"
        " and, once exhausted, ends among the dead letters instead of vanishing.",
    )
    mode.add_argument(
        "--publish",
        "--pubblica",
        action="store_true",
        help="Publishes an event and prints its identifier. First half of criterion (c), to run"
        " with the gateway down.",
    )
    mode.add_argument(
        "--notification-event-test",
        "--prova-evento-notifica",
        action="store_true",
        help="Publishes an event in the NotificationService form and checks that it is delivered",
    )
    mode.add_argument(
        "--boundary-test",
        "--prova-confine",
        action="store_true",
        help="Criterion (d): tries to read the monolith tables and checks that it is refused,"
        " while the four views answer.",
    )
    mode.add_argument(
        "--wait",
        "--attendi",
        dest="wait_for",
        metavar="EVENT",
        help="Waits until the given event is delivered. Second half of criterion (c), to run"
        " after the replicas are back.",
    )

    args = parser.parse_args()

    if args.retry_test:
        return asyncio.run(retry_test(args))
    if args.publish:
        return asyncio.run(publish_only(args))
    if args.notification_event_test:
        return asyncio.run(notification_event_test(args))
    if args.boundary_test:
        return asyncio.run(boundary_test(args))
    if args.wait_for:
        return asyncio.run(wait_only(args))
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
