"""The burst of scenario 4.1: N notifications written into the NotificationService with a given concurrency.

    python burst.py --recipients 2000 --concurrency 20

Prints one BURST line with marker, counts, start, end, duration and the p50/p95/max latency per write.
It does not wait for the delivery and does not clean up: see deploy/experiments/phase5/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# The provider of the scenario, a forum post notice, declared by the site.
COMPONENT = "mod_forum"
EVENT_TYPE = "posts"

# The administrator: the teacher who posts.
SENDER = 2


def environment() -> tuple[str, str, str]:
    """The coordinates the service receives, read from the same environment."""
    database_url = os.environ.get("NS_DATABASE_URL")
    if not database_url:
        raise SystemExit("NS_DATABASE_URL must be set in the environment.")
    return (
        database_url,
        os.environ.get("NS_SMOKE_BASE_URL", "http://notification-service:8080"),
        os.environ.get("NS_API_TOKEN", ""),
    )


def headers(token: str, key: str) -> dict[str, str]:
    """The headers with which the monolith presents itself to the service, idempotency
    key included.
    """
    values = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": key,
    }
    if token:
        values["Authorization"] = f"Bearer {token}"
    return values


async def recipients(engine, count: int) -> list[int]:
    """The first N users that receive notifications, from the view, by id, the sender
    excluded.
    """
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT user_id FROM user_view "
                " WHERE deleted = 0 AND email_stop = 0 AND user_id > :sender "
                " ORDER BY user_id LIMIT :count"
            ),
            {"sender": SENDER, "count": count},
        )
        found = [int(row[0]) for row in rows]

    if len(found) < count:
        raise SystemExit(f"The view offers {len(found)} recipients, {count} are needed.")
    return found


def percentile(values: list[float], which: float) -> float:
    """The value below which that fraction of the measures lies."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round(which * (len(ordered) - 1))
    return ordered[index]


async def run(args: argparse.Namespace) -> int:
    database_url, base_url, token = environment()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        people = await recipients(engine, args.recipients)
    finally:
        await engine.dispose()

    marker = uuid.uuid4().hex[:8]
    semaphore = asyncio.Semaphore(args.concurrency)
    durations_ms: list[float] = []
    failed: list[str] = []
    total = len(people)

    async def write(client: httpx.AsyncClient, index: int, person: int) -> None:
        body = {
            "user_id_from": SENDER,
            "user_id_to": person,
            "component": COMPONENT,
            "event_type": EVENT_TYPE,
            "subject": f"Burst {marker} {index}/{total}",
            "full_message": f"Forum notice, burst {marker}, recipient {index} of {total}.",
            "in_popup": True,
        }
        async with semaphore:
            started_at = time.perf_counter()
            try:
                response = await client.post(
                    "/api/v1/notifications",
                    json=body,
                    headers=headers(token, f"burst-{marker}-{index}"),
                )
                if response.status_code >= 300:
                    failed.append(f"{person}: HTTP {response.status_code} {response.text[:120]}")
            except Exception as error:
                failed.append(f"{person}: {error}")
            durations_ms.append((time.perf_counter() - started_at) * 1000)

    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    start = time.time()
    print(
        f"burst {marker}: {total} recipients, {args.concurrency} in parallel, "
        f"start {datetime.fromtimestamp(start, UTC).isoformat(timespec='seconds')}",
        flush=True,
    )
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, limits=limits) as client:
        await asyncio.gather(*(write(client, i, p) for i, p in enumerate(people, start=1)))
    end = time.time()

    for row in failed[:5]:
        print(f"  failed: {row}", file=sys.stderr)
    if len(failed) > 5:
        print(f"  ... and {len(failed) - 5} more", file=sys.stderr)

    print(f"end {datetime.fromtimestamp(end, UTC).isoformat(timespec='seconds')}", flush=True)
    # The last line: instants in epoch seconds and the latencies of the writer.
    print(
        f"BURST marker={marker} recipients={total} sent={total - len(failed)} "
        f"failed={len(failed)} start={int(start)} end={int(end)} duration_s={end - start:.1f} "
        f"p50_ms={percentile(durations_ms, 0.50):.0f} p95_ms={percentile(durations_ms, 0.95):.0f} "
        f"max_ms={max(durations_ms, default=0.0):.0f}",
        flush=True,
    )
    return 0 if not failed else 1


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="A burst of notifications towards the NotificationService, to measure the gateway."
    )
    parser.add_argument("--recipients", "--destinatari", type=int, default=2000, help="how many notifications, one per person")
    parser.add_argument("--concurrency", "--concorrenza", type=int, default=20, help="how many requests in parallel")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
