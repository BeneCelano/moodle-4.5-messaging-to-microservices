"""Initial seed of the service schema from the monolith schema, as INSERT ... SELECT inside the engine.

    python scripts/seed_from_monolith.py --schema moodle_db --prefix mdl_
    python scripts/seed_from_monolith.py --schema moodle_db --full

Ids are preserved; the auto-increment counters are moved past the highest id copied. The default
mode resumes from the highest id already present; --full rewrites the rows already copied.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402


@dataclass
class Mapping:
    """Mapping between a monolith table and a service table."""

    destination: str
    source: str
    columns: list[tuple[str, str]]
    """Pairs: destination column, selection expression on the source."""

    autoincrement: bool = True
    """Whether the destination assigns its own ids."""

    key: str = "id"
    """Column that orders the copy and drives the incremental resume; same name in both schemas."""


MAPPINGS: list[Mapping] = [
    Mapping(
        destination="conversations",
        source="message_conversations",
        columns=[
            ("id", "id"),
            ("type", "type"),
            ("name", "name"),
            ("conversation_hash", "convhash"),
            ("component", "component"),
            ("item_type", "itemtype"),
            ("item_id", "itemid"),
            ("context_id", "contextid"),
            ("enabled", "enabled"),
            ("time_created", "timecreated"),
            ("time_modified", "timemodified"),
        ],
    ),
    Mapping(
        destination="conversation_members",
        source="message_conversation_members",
        columns=[
            ("id", "id"),
            ("conversation_id", "conversationid"),
            ("user_id", "userid"),
            ("time_created", "timecreated"),
        ],
    ),
    Mapping(
        destination="conversation_actions",
        source="message_conversation_actions",
        columns=[
            ("id", "id"),
            ("user_id", "userid"),
            ("conversation_id", "conversationid"),
            ("action", "action"),
            ("time_created", "timecreated"),
        ],
    ),
    Mapping(
        destination="messages",
        source="messages",
        columns=[
            ("id", "id"),
            ("conversation_id", "conversationid"),
            ("user_id_from", "useridfrom"),
            ("subject", "subject"),
            ("full_message", "fullmessage"),
            ("full_message_format", "fullmessageformat"),
            ("full_message_html", "fullmessagehtml"),
            ("small_message", "smallmessage"),
            ("full_message_trust", "fullmessagetrust"),
            ("custom_data", "customdata"),
            ("time_created", "timecreated"),
        ],
    ),
    Mapping(
        destination="message_user_actions",
        source="message_user_actions",
        columns=[
            ("id", "id"),
            ("user_id", "userid"),
            ("message_id", "messageid"),
            ("action", "action"),
            ("time_created", "timecreated"),
        ],
    ),
    Mapping(
        destination="contacts",
        source="message_contacts",
        columns=[
            ("id", "id"),
            ("user_id", "userid"),
            ("contact_id", "contactid"),
            ("time_created", "timecreated"),
        ],
    ),
    Mapping(
        destination="contact_requests",
        source="message_contact_requests",
        columns=[
            ("id", "id"),
            ("user_id", "userid"),
            ("requested_user_id", "requesteduserid"),
            ("time_created", "timecreated"),
        ],
    ),
    Mapping(
        destination="users_blocked",
        source="message_users_blocked",
        columns=[
            ("id", "id"),
            ("user_id", "userid"),
            ("blocked_user_id", "blockeduserid"),
            ("time_created", "timecreated"),
        ],
    ),
]

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Outcome:
    """Outcome of the copy of one table."""

    copied: int = 0
    already_present: int = 0
    batches: int = 0


def build_statement(mapping: Mapping, schema: str, prefix: str, full: bool) -> str:
    """Copy statement for one batch: incremental ignores the rows already present, full
    rewrites them.
    """
    columns = ", ".join(f"`{name}`" for name, _ in mapping.columns)
    selected = ", ".join(expression for _, expression in mapping.columns)
    updates = ", ".join(
        f"`{name}` = VALUES(`{name}`)"
        for name, _ in mapping.columns
        if name != mapping.key
    )
    conflict = f" ON DUPLICATE KEY UPDATE {updates}" if full else ""
    ignore = "" if full else "IGNORE "

    return (
        f"INSERT {ignore}INTO `{mapping.destination}` ({columns}) "
        f"SELECT {selected} FROM `{schema}`.`{prefix}{mapping.source}` "
        f"WHERE `{mapping.key}` > :watermark ORDER BY `{mapping.key}` LIMIT :batch{conflict}"
    )


async def copy_table(engine, mapping: Mapping, schema: str, prefix: str, batch: int, full: bool) -> Outcome:
    """Copy one table in batches, resuming from what is already at the destination."""
    outcome = Outcome()
    statement = build_statement(mapping, schema, prefix, full)
    resume = mapping.key

    async with engine.begin() as connection:
        result = await connection.execute(
            text(f"SELECT COALESCE(MAX(`{resume}`), 0) FROM `{mapping.destination}`")
        )
        outcome.already_present = int(result.scalar() or 0)

    # Full mode starts over, to revisit what was already copied.
    watermark = 0 if full else outcome.already_present

    while True:
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), {"watermark": watermark, "batch": batch})
            inserted = result.rowcount or 0

            moved = await connection.execute(
                text(
                    f"SELECT COALESCE(MAX(`{resume}`), :watermark) FROM `{mapping.destination}` "
                    f"WHERE `{resume}` > :watermark"
                ),
                {"watermark": watermark},
            )
            highest = int(moved.scalar() or watermark)

        outcome.batches += 1
        outcome.copied += inserted

        # Stop when the highest id reached no longer advances (rowcount is unreliable
        # with ON DUPLICATE KEY UPDATE).
        if highest <= watermark:
            return outcome
        watermark = highest


async def reposition_counter(engine, mapping: Mapping) -> int | None:
    """Move the table counter past the highest id copied."""
    if not mapping.autoincrement:
        return None

    async with engine.begin() as connection:
        result = await connection.execute(
            text(f"SELECT COALESCE(MAX(`id`), 0) FROM `{mapping.destination}`")
        )
        following = int(result.scalar() or 0) + 1
        await connection.execute(
            text(f"ALTER TABLE `{mapping.destination}` AUTO_INCREMENT = {following}")
        )

    return following


async def run(schema: str, prefix: str, batch: int, full: bool, dry_run: bool) -> int:
    """Run the seed and report the outcome of each table."""
    if not IDENTIFIER.match(schema) or not IDENTIFIER.match(prefix.rstrip("_") or "x"):
        print("Invalid schema name or prefix.", file=sys.stderr)
        return 2

    if dry_run:
        print(f"Statements that would be executed, schema '{schema}', prefix '{prefix}':")
        print()
        for mapping in MAPPINGS:
            print(f"-- {mapping.source} -> {mapping.destination}")
            print(build_statement(mapping, schema, prefix, full))
            print()
        return 0

    # Imported here so that --dry-run needs no service environment.
    from app.core.config import get_settings
    from app.core.db import create_engine

    engine = create_engine(get_settings())
    total = 0

    print(f"Seeding from '{schema}' with prefix '{prefix}', batches of {batch} rows.")
    print("Mode: %s\n" % ("full" if full else "incremental"))
    print(f"{'table':<24} {'copied':>10} {'already present':>16} {'batches':>7}")
    print("-" * 60)

    try:
        for mapping in MAPPINGS:
            outcome = await copy_table(engine, mapping, schema, prefix, batch, full)
            total += outcome.copied
            print(
                f"{mapping.destination:<24} {outcome.copied:>10} "
                f"{outcome.already_present:>16} {outcome.batches:>7}"
            )

        print("\nRepositioning the counters:")
        for mapping in MAPPINGS:
            following = await reposition_counter(engine, mapping)
            if following is not None:
                print(f"  {mapping.destination:<24} next id: {following}")
    finally:
        await engine.dispose()

    print()
    print(f"Rows copied in this run: {total}")
    print("Re-running the script is harmless: it resumes where it stopped and duplicates nothing.")
    return 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Seeds the service schema from the monolith.")
    parser.add_argument("--schema", required=True, help="Monolith schema, e.g. moodle_db")
    parser.add_argument(
        "--prefix", default="mdl_", help="Moodle table prefix (default: mdl_)"
    )
    parser.add_argument(
        "--batch", type=int, default=5000, help="Rows per batch (default: 5000)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Walk the whole source again rewriting the rows already copied, to pick up the "
        "changes made after the first run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the statements without executing them")
    args = parser.parse_args()

    return asyncio.run(run(args.schema, args.prefix, args.batch, args.full, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
