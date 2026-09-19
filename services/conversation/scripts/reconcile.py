"""Compare the service database with the monolith database, and repair the differences on request.

    python scripts/reconcile.py --schema moodle_db --prefix mdl_ --check
    python scripts/reconcile.py --schema moodle_db --prefix mdl_ --apply

Requires the seed user. Exit code 0 when the two stores agree, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field

# The column mappings are declared in seed_from_monolith, which also sets sys.path.
from seed_from_monolith import IDENTIFIER, MAPPINGS, Mapping
from sqlalchemy import text

BARE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""Selection expressions that are plain column names."""


@dataclass
class Difference:
    """Differences found on one table."""

    missing: int = 0
    """At the source, absent at the destination."""

    differing: int = 0
    """In both, with at least one value different."""

    surplus: int = 0
    """At the destination, no longer at the source: deleted after the copy."""

    repaired: list[str] = field(default_factory=list)
    """Corrections applied."""

    @property
    def total(self) -> int:
        """Total number of discordant rows."""
        return self.missing + self.differing + self.surplus


def qualified(expression: str, alias: str) -> str:
    """Qualify plain column names with the source alias; expressions are left as they
    are.
    """
    return f"{alias}.`{expression}`" if BARE_COLUMN.match(expression) else expression


def source_table(mapping: Mapping, schema: str, prefix: str) -> str:
    """Fully qualified name of the source table."""
    return f"`{schema}`.`{prefix}{mapping.source}`"


def count_missing(mapping: Mapping, schema: str, prefix: str) -> str:
    """Rows at the source and absent at the destination."""
    return (
        f"SELECT COUNT(*) FROM {source_table(mapping, schema, prefix)} s "
        f"LEFT JOIN `{mapping.destination}` d ON d.`{mapping.key}` = s.`{mapping.key}` "
        f"WHERE d.`{mapping.key}` IS NULL"
    )


def count_surplus(mapping: Mapping, schema: str, prefix: str) -> str:
    """Rows at the destination and no longer at the source."""
    return (
        f"SELECT COUNT(*) FROM `{mapping.destination}` d "
        f"LEFT JOIN {source_table(mapping, schema, prefix)} s ON s.`{mapping.key}` = d.`{mapping.key}` "
        f"WHERE s.`{mapping.key}` IS NULL"
    )


def count_differing(mapping: Mapping, schema: str, prefix: str) -> str:
    """Rows in both with at least one value different."""
    comparisons = " OR ".join(
        f"NOT ({qualified(expression, 's')} <=> d.`{name}`)"
        for name, expression in mapping.columns
        if name != mapping.key
    )

    return (
        f"SELECT COUNT(*) FROM `{mapping.destination}` d "
        f"JOIN {source_table(mapping, schema, prefix)} s ON s.`{mapping.key}` = d.`{mapping.key}` "
        f"WHERE {comparisons}"
    )


def repair_upsert(mapping: Mapping, schema: str, prefix: str) -> str:
    """Reinsert the missing rows and rewrite the differing ones, in one statement."""
    columns = ", ".join(f"`{name}`" for name, _ in mapping.columns)
    selected = ", ".join(expression for _, expression in mapping.columns)
    updates = ", ".join(
        f"`{name}` = VALUES(`{name}`)" for name, _ in mapping.columns if name != mapping.key
    )

    return (
        f"INSERT INTO `{mapping.destination}` ({columns}) "
        f"SELECT {selected} FROM {source_table(mapping, schema, prefix)} "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )


def repair_delete(mapping: Mapping, schema: str, prefix: str) -> str:
    """Remove the rows that no longer exist at the source."""
    return (
        f"DELETE d FROM `{mapping.destination}` d "
        f"LEFT JOIN {source_table(mapping, schema, prefix)} s ON s.`{mapping.key}` = d.`{mapping.key}` "
        f"WHERE s.`{mapping.key}` IS NULL"
    )


async def inspect(engine, mapping: Mapping, schema: str, prefix: str) -> Difference:
    """Count the three kinds of difference on one table."""
    difference = Difference()
    async with engine.connect() as connection:
        for attribute, builder in (
            ("missing", count_missing),
            ("differing", count_differing),
            ("surplus", count_surplus),
        ):
            result = await connection.execute(text(builder(mapping, schema, prefix)))
            setattr(difference, attribute, int(result.scalar() or 0))

    return difference


async def run(schema: str, prefix: str, apply: bool, dry_run: bool) -> int:
    """Compare the two databases and, if asked, repair the differences."""
    if not IDENTIFIER.match(schema) or not IDENTIFIER.match(prefix.rstrip("_") or "x"):
        print("Invalid database name or prefix.", file=sys.stderr)
        return 2

    if dry_run:
        print(f"Queries that would be executed, database '{schema}', prefix '{prefix}':")
        for mapping in MAPPINGS:
            print(f"\n-- {mapping.destination}")
            print(count_missing(mapping, schema, prefix) + ";")
            print(count_differing(mapping, schema, prefix) + ";")
            print(count_surplus(mapping, schema, prefix) + ";")
        return 0

    # Imported here so that --dry-run needs no service environment.
    from app.core.config import get_settings
    from app.core.db import create_engine

    engine = create_engine(get_settings())
    differences: dict[str, Difference] = {}

    print(f"Comparison with '{schema}', prefix '{prefix}'.")
    print("Mode: %s\n" % ("repair" if apply else "check only"))
    print(f"{'table':<24} {'missing':>10} {'differing':>10} {'extra':>10}   outcome")
    print("-" * 74)

    try:
        for mapping in MAPPINGS:
            differences[mapping.destination] = await inspect(engine, mapping, schema, prefix)

        if apply:
            # Write first, then delete: inserts go from parent to child tables,
            # deletions the other way round.
            for mapping in MAPPINGS:
                difference = differences[mapping.destination]
                if difference.missing or difference.differing:
                    async with engine.begin() as connection:
                        await connection.execute(text(repair_upsert(mapping, schema, prefix)))
                    difference.repaired.append("reinserted and rewritten")

            for mapping in reversed(MAPPINGS):
                difference = differences[mapping.destination]
                if difference.surplus:
                    async with engine.begin() as connection:
                        await connection.execute(text(repair_delete(mapping, schema, prefix)))
                    difference.repaired.append("removed")

        for mapping in MAPPINGS:
            difference = differences[mapping.destination]
            # Missing and differing rows break the fidelity of the copy; surplus rows in
            # the service do not.
            if difference.total == 0:
                outcome = "identical"
            elif difference.repaired:
                outcome = ", ".join(difference.repaired)
            elif difference.missing or difference.differing:
                outcome = "to repair"
            else:
                outcome = "extra in the service"

            print(
                f"{mapping.destination:<24} {difference.missing:>10} "
                f"{difference.differing:>10} {difference.surplus:>10}   {outcome}"
            )
    finally:
        await engine.dispose()

    missing_total = sum(difference.missing for difference in differences.values())
    differing_total = sum(difference.differing for difference in differences.values())
    surplus_total = sum(difference.surplus for difference in differences.values())

    print()
    if missing_total == 0 and differing_total == 0 and surplus_total == 0:
        print("The two databases are identical on every copied table.")
        return 0

    if apply:
        print(f"Rows corrected: {missing_total + differing_total + surplus_total}.")
        print("Re-run with --check to confirm none remain.")
        return 0

    # Exit code 1 only for what breaks fidelity.
    if missing_total or differing_total:
        print(f"The copy is not faithful: {missing_total} rows missing, {differing_total} differing.")
        if surplus_total:
            print(
                f"There are also {surplus_total} rows present only in the service, "
                "which do not break fidelity."
            )
        print("Re-run with --apply to correct them.")
        return 1

    print("The copy is faithful: nothing is missing and nothing differs.")
    print(f"Rows present only in the service: {surplus_total}. Since the cutover the service receives")
    print("the writes and the monolith no longer sees them: this is not a misalignment.")
    print("To make the two stores identical again, knowing it means DELETING them from the service,")
    print("re-run with --apply.")
    return 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Compares the service database with the monolith database."
    )
    parser.add_argument("--schema", required=True, help="Monolith database, e.g. moodle_db")
    parser.add_argument(
        "--prefix", default="mdl_", help="Moodle table prefix (default: mdl_)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Report the differences without changing anything (default)",
    )
    mode.add_argument("--apply", action="store_true", help="Correct the differences found")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the queries without executing them"
    )
    args = parser.parse_args()

    return asyncio.run(run(args.schema, args.prefix, args.apply, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
