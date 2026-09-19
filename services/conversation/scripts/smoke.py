"""Full-chain smoke test of the ConversationService, on real infrastructure.

    python scripts/smoke.py --url http://conversation-service:8080          the chain
    python scripts/smoke.py --url http://conversation-service:8080 --keep   leave the rows
    python scripts/smoke.py --boundary-test                                 the monolith schema is unreachable

The last printed line is the one `verify.sh` reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid

import aio_pika
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

API = "/api/v1"
EVENT = "conversation.message.sent"
INDIVIDUAL_CONVERSATION = 1

# How long to wait for the relay to publish the event.
PUBLISH_WAIT = 15.0
QUEUE_WAIT = 10.0


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


async def two_users(engine) -> tuple[int, int]:
    """Two existing users, read from the view; ids 1 and 2 are Moodle's system user and
    guest.
    """
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT user_id FROM user_view WHERE deleted = 0 AND user_id > 2 ORDER BY user_id LIMIT 2")
        )
        users = [row.user_id for row in rows]
    if len(users) < 2:
        raise SystemExit("At least two non-deleted users are needed in the view: the test cannot run.")
    return users[0], users[1]


async def wait_for_publication(engine, event_id: str) -> dict | None:
    """Wait until the relay marks the event as published."""
    deadline = time.monotonic() + PUBLISH_WAIT
    while time.monotonic() < deadline:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT event_id, event_type, attempts, published_at "
                        "  FROM outbox WHERE event_id = :e"
                    ).bindparams(e=event_id)
                )
            ).first()
        if row is not None and row.published_at is not None:
            return {"attempts": row.attempts, "event_type": row.event_type}
        await asyncio.sleep(0.5)
    return None


async def run(args: argparse.Namespace) -> int:
    """Walk the chain and return the exit code."""
    outcome = Outcome()

    database_url = os.environ.get("CS_DATABASE_URL")
    rabbitmq_url = os.environ.get("CS_RABBITMQ_URL")
    exchange_name = os.environ.get("CS_RABBITMQ_EXCHANGE", "moodle.messaging")
    token = os.environ.get("CS_API_TOKEN", "")

    if not database_url or not rabbitmq_url:
        raise SystemExit("CS_DATABASE_URL and CS_RABBITMQ_URL must be set in the environment.")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    conversation_id: int | None = None

    # The broker is attempted, not required: the checks that do not need it still run.
    connection = None
    queue = None
    broker_fault = ""
    try:
        connection = await asyncio.wait_for(aio_pika.connect_robust(rabbitmq_url), timeout=10.0)
        channel = await connection.channel()
        exchange = await channel.declare_exchange(exchange_name, aio_pika.ExchangeType.TOPIC, durable=True)

        # Declared and bound before sending: an exchange keeps nothing while no queue is
        # bound.
        queue = await channel.declare_queue("", exclusive=True, auto_delete=True)
        await queue.bind(exchange, routing_key=EVENT)
    except Exception as error:
        broker_fault = str(error) or error.__class__.__name__

    try:
        async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=15.0) as http:

            # --- 1. The service reaches its dependencies --------------------------------------------
            try:
                response = await http.get("/readyz")
                checks = response.json().get("checks", {})
                outcome.check(
                    checks.get("database") == "ok" and checks.get("broker") == "ok",
                    "The service reaches the database and the broker",
                    f"database: {checks.get('database')}, broker: {checks.get('broker')}",
                )
            except Exception as error:
                outcome.check(False, "The service reaches the database and the broker", str(error))
                return 1

            sender, recipient = await two_users(engine)

            # The conversation is the only thing the test owns.
            response = await http.post(
                f"{API}/conversations",
                json={"type": INDIVIDUAL_CONVERSATION, "user_ids": [sender, recipient]},
            )
            if response.status_code not in (200, 201):
                outcome.check(False, "Setup: scratch conversation", f"HTTP {response.status_code}")
                return 1
            conversation_id = response.json()["id"]

            # --- 2. Send on an individual conversation ---------------------------------------------
            key = f"smoke-{uuid.uuid4()}"
            message_text = f"Full-chain smoke test, {time.strftime('%Y-%m-%d %H:%M:%S')}"
            response = await http.post(
                f"{API}/conversations/{conversation_id}/messages",
                json={"user_id_from": sender, "text": message_text, "format": 0},
                headers={"Idempotency-Key": key},
            )
            created = response.status_code == 201
            message = response.json() if response.status_code < 400 else {}
            message_id = message.get("id")

            async with engine.connect() as conn:
                present = (
                    await conn.execute(
                        text("SELECT id FROM messages WHERE id = :i").bindparams(i=message_id or 0)
                    )
                ).first()
            outcome.check(
                created and present is not None,
                "Send on an individual conversation",
                f"HTTP {response.status_code}, message {message_id} persisted",
            )

            # --- 3. Event recorded in the same transaction -----------------------------------------
            async with engine.connect() as conn:
                event = (
                    await conn.execute(
                        text(
                            "SELECT event_id, event_type, attempts FROM outbox "
                            " WHERE JSON_EXTRACT(envelope, '$.payload.message_id') = :m "
                            " ORDER BY id DESC LIMIT 1"
                        ).bindparams(m=message_id or 0)
                    )
                ).first()
            outcome.check(
                event is not None and event.event_type == EVENT,
                "Event recorded in the same transaction",
                f"row in the outbox, type {event.event_type if event else 'absent'}"
                if event
                else "no row",
            )

            # --- 4. Handed to the broker -----------------------------------------------------------
            published = await wait_for_publication(engine, event.event_id) if event else None
            outcome.check(
                published is not None,
                "Handed to the broker",
                f"confirmation recorded after {published['attempts']} attempt(s)"
                if published
                else "not published",
            )

            # --- 7. Resend with the same idempotency key -------------------------------------------
            repeated = await http.post(
                f"{API}/conversations/{conversation_id}/messages",
                json={"user_id_from": sender, "text": message_text, "format": 0},
                headers={"Idempotency-Key": key},
            )
            same_id = repeated.json().get("id") if repeated.status_code < 400 else None
            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) AS n FROM messages "
                            " WHERE conversation_id = :c AND small_message = :t"
                        ).bindparams(c=conversation_id, t=message_text)
                    )
                ).scalar_one()
            outcome.check(
                same_id == message_id and count == 1,
                "Resent with the same idempotency key",
                f"same identifier {same_id}, {count} message(s) in the table",
            )

        # --- 5, 6, 8. What reached the queue -------------------------------------------------------
        #
        # One read serves three checks: the event arrived, it carries the recipients, no second one.
        if queue is None:
            # Without the broker these three are reported as failed, with the reason.
            for name in (
                "The event reaches the queue",
                "The recipients travel inside the event",
                "Idempotency also covers publication",
            ):
                outcome.check(False, name, f"broker unreachable: {broker_fault}")
            outcome.summary()
            return 1

        envelopes: list[dict] = []
        deadline = time.monotonic() + QUEUE_WAIT
        while time.monotonic() < deadline:
            queued = await queue.get(no_ack=True, fail=False)
            if queued is None:
                if envelopes:
                    # Allow a moment for a second event, which must not exist.
                    await asyncio.sleep(1.0)
                    if await queue.get(no_ack=True, fail=False) is None:
                        break
                await asyncio.sleep(0.3)
                continue
            envelopes.append(json.loads(queued.body))

        outcome.check(
            len(envelopes) >= 1 and envelopes[0].get("event_type") == EVENT,
            "The event reaches the queue",
            f"envelope read, type {envelopes[0].get('event_type')}" if envelopes else "no envelope",
        )

        recipients = envelopes[0].get("payload", {}).get("recipient_ids", []) if envelopes else []
        outcome.check(
            recipient in recipients and sender not in recipients,
            "The recipients travel inside the event",
            f"recipient_ids = {recipients}",
        )

        outcome.check(
            len(envelopes) == 1,
            "Idempotency also covers publication",
            f"{len(envelopes)} event(s) in the queue",
        )

    finally:
        # Cleanup runs even after a failed check.
        if conversation_id is not None and not args.keep:
            try:
                async with httpx.AsyncClient(
                    base_url=args.url, headers=headers, timeout=15.0
                ) as http:
                    await http.delete(f"{API}/conversations/{conversation_id}")
                print(f"\nCleanup: conversation {conversation_id} removed.")
            except Exception as error:
                print(
                    f"\nWARNING: cleanup failed, "
                    f"conversation {conversation_id} left behind: {error}"
                )
        elif conversation_id is not None:
            print(f"\nKept on request: conversation {conversation_id}.")
        if connection is not None:
            await connection.close()
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


async def boundary_test(args: argparse.Namespace) -> int:
    """Check that the monolith schema is refused and that the view answers."""
    database_url = os.environ.get("CS_DATABASE_URL")
    if not database_url:
        raise SystemExit("CS_DATABASE_URL must be set in the environment.")

    outcome = Outcome()
    engine = create_async_engine(database_url, pool_pre_ping=True)

    schema = os.environ.get("CS_SMOKE_MONOLITH_SCHEMA", "moodle_db")
    prefix = os.environ.get("CS_SMOKE_MONOLITH_PREFIX", "mdl_")

    print(f"Boundary test towards schema '{schema}'\n", flush=True)

    try:
        # Users are reachable only through the view; the monolith copies of
        # conversations and messages must be refused.
        for table in ("user", "message_conversations", "messages"):
            name = f"{schema}.{prefix}{table}"
            try:
                async with engine.connect() as connection:
                    await connection.execute(text(f"SELECT 1 FROM {name} LIMIT 1"))
                outcome.check(
                    False, f"direct read of {table} is refused", "succeeded"
                )
            except Exception as error:
                # Refusal is the expected outcome; the message is kept to distinguish it
                # from a syntax error.
                reason = str(error)
                denied = "denied" in reason.lower() or "1142" in reason or "1044" in reason
                outcome.check(
                    denied,
                    f"direct read of {table} is refused",
                    "" if denied else reason[:70],
                )

        try:
            async with engine.connect() as connection:
                rows = await connection.execute(text("SELECT COUNT(*) FROM user_view"))
                count = int(rows.scalar() or 0)
            outcome.check(count > 0, "the user_view view answers", f"{count} rows")
        except Exception as error:
            outcome.check(False, "the user_view view answers", str(error)[:70])
    finally:
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


def main() -> int:
    """Parse the arguments and run."""
    parser = argparse.ArgumentParser(description="Full-chain smoke test of the ConversationService.")
    parser.add_argument(
        "--url",
        default="http://conversation-service:8080",
        help="Service address. The default is the in-cluster name.",
    )
    parser.add_argument(
        "--keep",
        "--conserva",
        action="store_true",
        help="Keep the conversation created by the test.",
    )
    parser.add_argument(
        "--boundary-test",
        "--prova-confine",
        action="store_true",
        help="Check that the monolith schema is not readable and that the view is.",
    )
    args = parser.parse_args()

    if args.boundary_test:
        return asyncio.run(boundary_test(args))

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
