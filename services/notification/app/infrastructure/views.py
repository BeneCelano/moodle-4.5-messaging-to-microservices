"""Reading of the facts that belong to the monolith."""

from __future__ import annotations

import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.entities import Recipient

logger = logging.getLogger(__name__)


class ViewReader:
    """Read the two views on the monolith schema."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        site_settings_ttl_seconds: int = 300,
        site_wwwroot: str = "",
    ) -> None:
        self._session_factory = session_factory
        self._ttl = site_settings_ttl_seconds
        # Without the trailing slash, as Moodle writes $CFG->wwwroot.
        self._wwwroot = site_wwwroot.rstrip("/")
        self._site: dict[str, str] | None = None
        self._site_read_at = 0.0

    async def recipients(self, user_ids: list[int]) -> dict[int, Recipient]:
        """Return the recipients found, indexed by identifier."""
        if not user_ids:
            return {}

        placeholders = ", ".join(f":u{i}" for i in range(len(user_ids)))
        params = {f"u{i}": user_id for i, user_id in enumerate(user_ids)}

        async with self._session_factory() as session:
            rows = await session.execute(
                text(
                    "SELECT user_id, first_name, last_name, lang, email_stop, deleted "
                    "  FROM user_view "
                    f" WHERE user_id IN ({placeholders})"
                ),
                params,
            )

            return {
                row.user_id: Recipient(
                    user_id=row.user_id,
                    first_name=row.first_name or "",
                    last_name=row.last_name or "",
                    lang=row.lang or "",
                    email_stop=bool(row.email_stop),
                    deleted=bool(row.deleted),
                )
                for row in rows
            }

    async def site_settings(self) -> dict[str, str]:
        """Return the site settings, remembering them for the configured time."""
        now = time.monotonic()
        if self._site is not None and now - self._site_read_at < self._ttl:
            return self._site

        async with self._session_factory() as session:
            rows = await session.execute(
                text("SELECT setting_name, setting_value FROM site_view")
            )
            self._site = {row.setting_name: row.setting_value for row in rows}

            # `wwwroot` comes from the service configuration (in Moodle it lives in
            # config.php, not in `mdl_config`) and is merged with setdefault.
            if self._wwwroot:
                self._site.setdefault("wwwroot", self._wwwroot)

            self._site_read_at = now
            return self._site
