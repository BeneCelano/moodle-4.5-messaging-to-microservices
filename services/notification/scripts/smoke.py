"""Full-chain smoke test of the NotificationService, run as a Job with the service image.

    smoke.py                          the chain: dependencies, write, reads, event, idempotency
    smoke.py --contact-request-test   the contact request event consumed and republished
    smoke.py --boundary-test          the monolith schema stays unreachable
    smoke.py --publish                write a notification and print its id, without waiting
    smoke.py --wait <id>              wait until that id has been published

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
from datetime import UTC, datetime

import aio_pika
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PRODUCED_EVENT = "notification.created"
CONSUMED_EVENT = "conversation.contactrequest.created"
COMPONENT = "moodle"
CONTACT_REQUEST_TYPE = "messagecontactrequests"

# How long to wait for the relay to move an event from the outbox to the broker.
PUBLISH_WAIT = 20.0

# How long to wait for the service to consume the contact request event and write the
# notification.
CONSUME_WAIT = 25.0

# How long to wait before concluding that a repetition produced nothing.
IDEMPOTENCY_WAIT = 6.0


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


# ---------------------------------------------------------------------------------------------------
# Parts shared by the modes
# ---------------------------------------------------------------------------------------------------


def environment() -> tuple[str, str, str, str, str]:
    """The coordinates the service receives, read from the same environment."""
    database_url = os.environ.get("NS_DATABASE_URL")
    rabbitmq_url = os.environ.get("NS_RABBITMQ_URL")
    if not database_url or not rabbitmq_url:
        raise SystemExit("NS_DATABASE_URL and NS_RABBITMQ_URL must be defined in the environment.")

    return (
        database_url,
        rabbitmq_url,
        os.environ.get("NS_RABBITMQ_EXCHANGE", "moodle.messaging"),
        os.environ.get("NS_SMOKE_BASE_URL", "http://notification-service:8080"),
        os.environ.get("NS_API_TOKEN", ""),
    )


def headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
    """The headers with which the monolith presents itself to the service."""
    values = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        values["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        values["Idempotency-Key"] = idempotency_key
    return values


async def test_recipient(engine) -> int:
    """Pick from the view a person who receives notifications."""
    async with engine.connect() as connection:
        row = await connection.execute(
            text(
                "SELECT user_id FROM user_view "
                " WHERE deleted = 0 AND email_stop = 0 AND user_id > 1 "
                " ORDER BY user_id LIMIT 1"
            )
        )
        found = row.scalar()

    if found is None:
        raise Interrupted("The users view offers no usable recipient.")

    return int(found)


async def open_exchange(connection, exchange_name: str):
    """Declare the exchange with the same parameters as the producer: a different declaration would be
    refused by the broker, better here than in production.
    """
    channel = await connection.channel(publisher_confirms=True)
    return await channel.declare_exchange(exchange_name, aio_pika.ExchangeType.TOPIC, durable=True)


async def wait_for(condition, deadline: float, interval: float = 0.5) -> bool:
    """Wait until an asynchronous condition becomes true, within the given time."""
    limit = time.monotonic() + deadline
    while time.monotonic() < limit:
        if await condition():
            return True
        await asyncio.sleep(interval)
    return False


async def event_published(engine, notification_id: int) -> bool:
    """Whether the event of that notification was delivered to the broker."""
    async with engine.connect() as connection:
        row = await connection.execute(
            text(
                "SELECT published_at FROM outbox "
                " WHERE event_type = :t "
                "   AND JSON_EXTRACT(envelope, '$.payload.notification_id') = :n"
            ),
            {"t": PRODUCED_EVENT, "n": notification_id},
        )
        value = row.scalar()
    return value is not None


async def cleanup(engine, notification_id: int) -> None:
    """Remove the rows produced by a test."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "DELETE FROM outbox "
                " WHERE JSON_EXTRACT(envelope, '$.payload.notification_id') = :n"
            ),
            {"n": notification_id},
        )
        await connection.execute(
            text("DELETE FROM notifications WHERE id = :n"), {"n": notification_id}
        )


def contact_request_envelope(event_id: str, requester: int, requested: int) -> dict:
    """Build the envelope the ConversationService publishes."""
    return {
        "event_id": event_id,
        "event_type": CONSUMED_EVENT,
        "event_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "conversation-service",
        "correlation_id": None,
        "payload": {
            "request_id": 0,
            "user_id": requester,
            "requested_user_id": requested,
            "time_created": int(time.time()),
            "source_component": COMPONENT,
            "source_event_name": CONTACT_REQUEST_TYPE,
        },
    }


# ---------------------------------------------------------------------------------------------------
# Default mode: the chain
# ---------------------------------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    """Walk the chain and report the outcome of each step."""
    database_url, rabbitmq_url, exchange, base_url, token = environment()
    outcome = Outcome()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    marker = uuid.uuid4().hex[:12]
    written: int | None = None

    print(f"Chain test, marker {marker}\n", flush=True)

    try:
        # -- 1. the dependencies ------------------------------------------------------------------
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            outcome.check(True, "database reachable")
        except Exception as error:
            outcome.check(False, "database reachable", str(error)[:80])
            raise Interrupted from error

        try:
            broker_connection = await aio_pika.connect_robust(rabbitmq_url)
            await broker_connection.close()
            outcome.check(True, "broker reachable")
        except Exception as error:
            outcome.check(False, "broker reachable", str(error)[:80])
            raise Interrupted from error

        # -- 2. the views on the monolith ---------------------------------------------------------
        try:
            async with engine.connect() as connection:
                users = await connection.execute(text("SELECT COUNT(*) FROM user_view"))
                user_count = int(users.scalar() or 0)
                site = await connection.execute(
                    text("SELECT setting_name, setting_value FROM site_view")
                )
                settings = {r.setting_name: r.setting_value for r in site}
            outcome.check(
                user_count > 0, "the users view answers", f"{user_count} rows"
            )
            # Two settings: `wwwroot` comes from the ConfigMap, not from the view.
            missing = {"sitename", "fullnamedisplay"} - set(settings)
            empty = sorted(k for k, v in settings.items() if not v)
            outcome.check(
                not missing and not empty,
                "the site settings view answers",
                "missing " + ", ".join(sorted(missing)) if missing
                else ("empty: " + ", ".join(empty) if empty else f"{len(settings)} settings"),
            )
            address = os.environ.get("NS_SITE_WWWROOT", "")
            outcome.check(
                bool(address),
                "the site address is configured",
                address or "NS_SITE_WWWROOT not set: links would be born relative",
            )
        except Exception as error:
            outcome.check(False, "the views on the monolith answer", str(error)[:80])
            raise Interrupted from error

        # -- 3. the templates ---------------------------------------------------------------------
        async with engine.connect() as connection:
            templates = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM notification_templates "
                    " WHERE component = :c AND event_type = :e"
                ),
                {"c": COMPONENT, "e": CONTACT_REQUEST_TYPE},
            )
            template_count = int(templates.scalar() or 0)
        outcome.check(
            template_count > 0,
            "the templates have been seeded",
            f"{template_count} languages"
            if template_count
            else "run seed_notification_templates.php",
        )

        # -- 4. the recipient ---------------------------------------------------------------------
        recipient = await test_recipient(engine)
        outcome.check(True, "recipient chosen from the view", f"user {recipient}")

        async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
            # -- 5. the write ---------------------------------------------------------------------
            body = {
                "user_id_from": 2,
                "user_id_to": recipient,
                "component": "mod_assign",
                "event_type": "assign_notification",
                "subject": f"Smoke test {marker}",
                "full_message": f"Body of the test {marker}",
                "small_message": f"Test {marker}",
                "custom_data": json.dumps({"smoke": marker}),
                "in_popup": True,
            }
            key = f"smoke-{marker}"

            response = await client.post(
                "/api/v1/notifications", json=body, headers=headers(token, key)
            )
            if response.status_code != 201:
                outcome.check(
                    False, "the notification is written", f"HTTP {response.status_code}"
                )
                raise Interrupted
            written = int(response.json()["id"])
            outcome.check(True, "the notification is written", f"identifier {written}")

            # -- 6. the reads ---------------------------------------------------------------------
            entries = await client.get(
                f"/api/v1/users/{recipient}/notifications",
                params={"read": "false", "limit_num": 50},
                headers=headers(token),
            )
            # The status code is checked, to tell "not shown" from "the read failed".
            found_rows = [
                n for n in entries.json().get("notifications", []) if n["id"] == written
            ]
            outcome.check(
                entries.status_code == 200 and bool(found_rows),
                "it appears among the person's unread notifications",
                f"HTTP {entries.status_code}: {entries.text[:100]}" if entries.status_code != 200
                else f"{len(entries.json().get('notifications', []))} unread in the window",
            )

            popup = await client.get(
                f"/api/v1/users/{recipient}/notifications/popup",
                params={"limit_num": 50},
                headers=headers(token),
            )
            in_popup_rows = [
                n for n in popup.json().get("notifications", []) if n["id"] == written
            ]
            outcome.check(
                popup.status_code == 200 and bool(in_popup_rows),
                "it appears in the popup panel",
                f"HTTP {popup.status_code}: {popup.text[:100]}"
                if popup.status_code != 200
                else f"{len(popup.json().get('notifications', []))} in the panel in the window",
            )

            # -- 7. the event ---------------------------------------------------------------------
            published = await wait_for(
                lambda: event_published(engine, written), PUBLISH_WAIT
            )
            outcome.check(
                published,
                "the event was handed to the broker",
                "" if published else f"no publication within {PUBLISH_WAIT:.0f}s",
            )

            # -- 8. idempotency: the same key must not produce a second notification --------------
            repeated = await client.post(
                "/api/v1/notifications", json=body, headers=headers(token, key)
            )
            same = repeated.status_code == 201 and int(repeated.json()["id"]) == written
            outcome.check(
                same,
                "a repeated write does not produce a second one",
                f"identifier {repeated.json().get('id')}" if not same else "",
            )

            await asyncio.sleep(IDEMPOTENCY_WAIT)
            async with engine.connect() as connection:
                count = await connection.execute(
                    text(
                        "SELECT COUNT(*) FROM notifications "
                        " WHERE user_id_to = :u AND subject = :s"
                    ),
                    {"u": recipient, "s": body["subject"]},
                )
                total = int(count.scalar() or 0)
            outcome.check(
                total == 1, "only one exists in the store", f"{total} rows with that subject"
            )

            # -- 9. the deduplication of the modules ----------------------------------------------
            exists = await client.get(
                f"/api/v1/users/{recipient}/notifications/exists",
                params={"custom_data": body["custom_data"], "component": "mod_assign"},
                headers=headers(token),
            )
            outcome.check(
                bool(exists.json().get("exists")),
                "the activity modules' question finds the notification",
            )

            # -- 10. personal data: existence only, the test does not export or erase -------------
            personal_exists = await client.get(
                f"/api/v1/users/{recipient}/personal-data/exists",
                headers=headers(token),
            )
            outcome.check(
                bool(personal_exists.json().get("exists")),
                "the site declares it holds data about this person",
            )


            # -- 11. the read state ---------------------------------------------------------------
            before = await client.get(
                f"/api/v1/users/{recipient}/notifications/unread-count",
                headers=headers(token),
            )
            await client.post(
                f"/api/v1/notifications/{written}/read", headers=headers(token)
            )
            after = await client.get(
                f"/api/v1/users/{recipient}/notifications/unread-count",
                headers=headers(token),
            )
            drop = int(before.json()["count"]) - int(after.json()["count"])
            outcome.check(
                drop == 1, "marking it read lowers the count by one", f"drop {drop}"
            )

            # Marking it again changes nothing.
            again = await client.post(
                f"/api/v1/notifications/{written}/read", headers=headers(token)
            )
            outcome.check(
                again.status_code == 200 and again.json()["changed"] is False,
                "marking it again is harmless and does not rewrite the instant",
            )

    except Interrupted:
        print("\nThe chain stopped: the following checks would say nothing.")
    except Exception as error:
        # An unexpected fault is reported like a failed check, so that the summary is
        # still printed.
        outcome.check(
            False,
            f"the test stopped: {type(error).__name__}",
            str(error)[:120] or "no message",
        )
    finally:
        if written is not None:
            await cleanup(engine, written)
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------
# Mode: the two extracted services talk
# ---------------------------------------------------------------------------------------------------


async def contact_request_test(args: argparse.Namespace) -> int:
    """Publish the ConversationService event and check that the notification is born
    from it.
    """
    database_url, rabbitmq_url, exchange, _, _ = environment()
    outcome = Outcome()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    event_id = str(uuid.uuid4())
    born: int | None = None

    print(f"Event consumption test, envelope {event_id}\n", flush=True)

    try:
        recipient = await test_recipient(engine)
        requester = 2

        async with engine.connect() as connection:
            templates = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM notification_templates "
                    " WHERE component = :c AND event_type = :e"
                ),
                {"c": COMPONENT, "e": CONTACT_REQUEST_TYPE},
            )
            if not int(templates.scalar() or 0):
                outcome.check(
                    False,
                    "the templates have been seeded",
                    "run seed_notification_templates.php",
                )
                raise Interrupted
        outcome.check(True, "the templates have been seeded")

        broker_connection = await aio_pika.connect_robust(rabbitmq_url)
        try:
            exchange_obj = await open_exchange(broker_connection, exchange)
            envelope = contact_request_envelope(event_id, requester, recipient)
            await exchange_obj.publish(
                aio_pika.Message(
                    body=json.dumps(envelope, ensure_ascii=False).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=event_id,
                    type=CONSUMED_EVENT,
                ),
                routing_key=CONSUMED_EVENT,
            )
            outcome.check(True, "the event was published", f"user {recipient}")
        finally:
            await broker_connection.close()

        # -- the consumption ----------------------------------------------------------------------
        async def consumed() -> bool:
            async with engine.connect() as connection:
                row = await connection.execute(
                    text("SELECT result FROM processed_events WHERE event_id = :e"),
                    {"e": event_id},
                )
                return row.scalar() is not None

        happened = await wait_for(consumed, CONSUME_WAIT)
        outcome.check(
            happened,
            "the service consumed the event",
            "" if happened else f"no consumption within {CONSUME_WAIT:.0f}s",
        )
        if not happened:
            raise Interrupted

        # -- the notification ---------------------------------------------------------------------
        async with engine.connect() as connection:
            row = await connection.execute(
                text("SELECT result FROM processed_events WHERE event_id = :e"), {"e": event_id}
            )
            result = row.scalar()

        result = json.loads(result) if isinstance(result, str) else (result or {})
        born = result.get("notification_id")
        outcome.check(born is not None, "consumption produced a notification", f"id {born}")
        if born is None:
            raise Interrupted

        async with engine.connect() as connection:
            row = await connection.execute(
                text(
                    "SELECT user_id_to, user_id_from, component, event_type, subject, "
                    "       full_message, full_message_html, context_url, custom_data "
                    "  FROM notifications WHERE id = :n"
                ),
                {"n": born},
            )
            notification = row.mappings().first()

        outcome.check(
            notification["user_id_to"] == recipient and notification["user_id_from"] == requester,
            "the notification goes to the right person and comes from the right person",
        )
        outcome.check(
            notification["component"] == COMPONENT
            and notification["event_type"] == CONTACT_REQUEST_TYPE,
            "it carries the message name that delivery looks for",
            f"{notification['component']}/{notification['event_type']}",
        )

        # The text is composed: no placeholders of the template left.
        composed = (
            bool(notification["subject"])
            and bool(notification["full_message"])
            and "{$a" not in (notification["subject"] or "")
            and "{$a" not in (notification["full_message_html"] or "")
        )
        outcome.check(
            composed,
            "the text was composed and carries no placeholders",
            (notification["subject"] or "")[:60],
        )

        outcome.check(
            bool(notification["context_url"]) and "contactrequests" in (notification["context_url"] or ""),
            "it carries the address of the page on which to answer",
        )

        labels = json.loads(notification["custom_data"]) if notification["custom_data"] else {}
        outcome.check(
            bool(labels.get("actionbuttons")),
            "it carries the translated labels of the two buttons",
            ", ".join(sorted((labels.get("actionbuttons") or {}).values()))[:40],
        )

        # -- the event that follows ---------------------------------------------------------------
        published = await wait_for(lambda: event_published(engine, born), PUBLISH_WAIT)
        outcome.check(
            published,
            "delivery was announced in turn",
            "" if published else f"no publication within {PUBLISH_WAIT:.0f}s",
        )

        # -- the idempotency of the receiver ------------------------------------------------------
        broker_connection = await aio_pika.connect_robust(rabbitmq_url)
        try:
            exchange_obj = await open_exchange(broker_connection, exchange)
            await exchange_obj.publish(
                aio_pika.Message(
                    body=json.dumps(envelope, ensure_ascii=False).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=event_id,
                    type=CONSUMED_EVENT,
                ),
                routing_key=CONSUMED_EVENT,
            )
        finally:
            await broker_connection.close()

        await asyncio.sleep(IDEMPOTENCY_WAIT)
        async with engine.connect() as connection:
            count = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM notifications "
                    " WHERE user_id_to = :u AND user_id_from = :f AND event_type = :e "
                    "   AND time_created >= :t"
                ),
                {
                    "u": recipient,
                    "f": requester,
                    "e": CONTACT_REQUEST_TYPE,
                    "t": envelope["payload"]["time_created"] - 5,
                },
            )
            total = int(count.scalar() or 0)
        outcome.check(
            total == 1,
            "the same event delivered twice does not produce two",
            f"{total} notifications",
        )

    except Interrupted:
        print("\nThe test stopped: the following checks would say nothing.")
    except Exception as error:
        # An unexpected fault is reported, so that the summary is still printed.
        outcome.check(
            False,
            f"the test stopped: {type(error).__name__}",
            str(error)[:120] or "no message",
        )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM processed_events WHERE event_id = :e"), {"e": event_id}
            )
            if born is not None:
                await connection.execute(
                    text(
                        "DELETE FROM outbox "
                        " WHERE JSON_EXTRACT(envelope, '$.payload.notification_id') = :n"
                    ),
                    {"n": born},
                )
                await connection.execute(
                    text("DELETE FROM notifications WHERE id = :n"), {"n": born}
                )
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------
# Mode: the boundary
# ---------------------------------------------------------------------------------------------------


async def boundary_test(args: argparse.Namespace) -> int:
    """Check that the monolith schema is refused and that the views answer."""
    database_url, *_ = environment()
    outcome = Outcome()
    engine = create_async_engine(database_url, pool_pre_ping=True)

    schema = os.environ.get("NS_SMOKE_MONOLITH_SCHEMA", "moodle_db")
    prefix = os.environ.get("NS_SMOKE_MONOLITH_PREFIX", "mdl_")

    print(f"Boundary test towards schema '{schema}'\n", flush=True)

    try:
        for table in ("user", "notifications", "config"):
            name = f"{schema}.{prefix}{table}"
            try:
                async with engine.connect() as connection:
                    await connection.execute(text(f"SELECT 1 FROM {name} LIMIT 1"))
                outcome.check(False, f"direct read of {table} is refused", "succeeded")
            except Exception as error:
                # Refusal is the expected outcome; the message is kept to distinguish it
                # from a syntax error.
                text_body = str(error)
                denied = "denied" in text_body.lower() or "1142" in text_body or "1044" in text_body
                outcome.check(
                    denied,
                    f"direct read of {table} is refused",
                    "" if denied else text_body[:70],
                )

        for view in ("user_view", "site_view"):
            try:
                async with engine.connect() as connection:
                    rows = await connection.execute(text(f"SELECT COUNT(*) FROM {view}"))
                    count = int(rows.scalar() or 0)
                outcome.check(count > 0, f"the {view} view answers", f"{count} rows")
            except Exception as error:
                outcome.check(False, f"the {view} view answers", str(error)[:70])
    finally:
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------
# Modes: the two halves of the continuity criterion
# ---------------------------------------------------------------------------------------------------


async def publish_only(args: argparse.Namespace) -> int:
    """Write a notification and print its id, without waiting for the publication."""
    _, _, _, base_url, token = environment()
    database_url, *_ = environment()
    engine = create_async_engine(database_url, pool_pre_ping=True)

    try:
        recipient = await test_recipient(engine)
        marker = uuid.uuid4().hex[:12]

        async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
            response = await client.post(
                "/api/v1/notifications",
                json={
                    "user_id_from": 2,
                    "user_id_to": recipient,
                    "component": "mod_assign",
                    "event_type": "assign_notification",
                    "subject": f"Continuita' {marker}",
                    "full_message": f"Continuita' {marker}",
                    "in_popup": True,
                },
                headers=headers(token, f"continuita-{marker}"),
            )
            response.raise_for_status()
            written = int(response.json()["id"])
    finally:
        await engine.dispose()

    print(f"NOTIFICATION {written}")
    return 0


async def wait_only(args: argparse.Namespace) -> int:
    """Wait until the event of that notification is delivered to the broker."""
    database_url, *_ = environment()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    outcome = Outcome()
    notification_id = int(args.wait_for)

    try:
        delivered = await wait_for(
            lambda: event_published(engine, notification_id), PUBLISH_WAIT
        )
        outcome.check(
            delivered,
            "the event that survived the shutdown was handed over",
            f"notification {notification_id}",
        )
        if delivered:
            await cleanup(engine, notification_id)
    finally:
        await engine.dispose()

    outcome.summary()
    return 0 if outcome.failed_count == 0 else 1


# ---------------------------------------------------------------------------------------------------


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Full-chain smoke test of the NotificationService."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--contact-request-test",
        "--prova-richiesta-contatto",
        action="store_true",
        help="Publish the ConversationService event and check that the notification is born from it",
    )
    mode.add_argument(
        "--boundary-test",
        "--prova-confine",
        action="store_true",
        help="Check that the monolith schema is not readable and that the views are",
    )
    mode.add_argument(
        "--publish",
        "--pubblica",
        action="store_true",
        help="Write a notification and print its identifier, without waiting",
    )
    mode.add_argument(
        "--wait",
        "--attendi",
        dest="wait_for",
        metavar="ID",
        help="Wait until the event of that notification is published to the broker",
    )
    args = parser.parse_args()

    if args.contact_request_test:
        return asyncio.run(contact_request_test(args))
    if args.boundary_test:
        return asyncio.run(boundary_test(args))
    if args.publish:
        return asyncio.run(publish_only(args))
    if args.wait_for:
        return asyncio.run(wait_only(args))

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
