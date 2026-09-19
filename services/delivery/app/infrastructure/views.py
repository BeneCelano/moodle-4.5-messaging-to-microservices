"""Reading of the views on the monolith database, defined in `scripts/schema.sql` in
this service's schema: the gateway user has no privilege on `moodle_db`.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.channels import Recipient


@dataclass(frozen=True, slots=True)
class Device:
    """A device registered for push notifications."""

    device_id: int
    user_id: int
    app_id: str
    platform: str
    push_token: str
    enabled: int


class ViewReader:
    """Read-only access to the projections on the monolith."""

    def __init__(self, site_default_ttl_seconds: int = 60) -> None:
        self._site_default_ttl = site_default_ttl_seconds
        self._site_defaults: dict[str, str] | None = None
        self._site_defaults_read_at: float = 0.0

    async def recipients(self, session: AsyncSession, user_ids: Sequence[int]) -> dict[int, Recipient]:
        """Recipients, by identifier."""
        if not user_ids:
            return {}

        rows = await session.execute(
            text(
                "SELECT user_id, email, mail_format, auth_method, suspended, deleted, "
                "       email_stop, first_name, last_name "
                "  FROM recipient_view "
                " WHERE user_id IN :ids"
            ).bindparams(ids=tuple(user_ids)),
        )

        return {
            row.user_id: Recipient(
                user_id=row.user_id,
                email=row.email,
                mail_format=int(row.mail_format or 0),
                auth_method=row.auth_method or "",
                suspended=int(row.suspended or 0),
                deleted=int(row.deleted or 0),
                email_stop=int(row.email_stop or 0),
                first_name=row.first_name or "",
                last_name=row.last_name or "",
            )
            for row in rows
        }

    async def preferences(
        self, session: AsyncSession, user_ids: Sequence[int]
    ) -> dict[int, dict[str, str]]:
        """Channel preferences, grouped by user."""
        if not user_ids:
            return {}

        rows = await session.execute(
            text(
                "SELECT user_id, preference_name, preference_value "
                "  FROM channel_preference_view "
                " WHERE user_id IN :ids"
            ).bindparams(ids=tuple(user_ids)),
        )

        found_preferences: dict[int, dict[str, str]] = {}
        for row in rows:
            found_preferences.setdefault(row.user_id, {})[row.preference_name] = row.preference_value or ""
        return found_preferences

    async def site_defaults(self, session: AsyncSession) -> dict[str, str]:
        """Site defaults, cached for a short interval."""
        now = time.monotonic()
        if self._site_defaults is not None and now - self._site_defaults_read_at < self._site_default_ttl:
            return self._site_defaults

        rows = await session.execute(text("SELECT setting_name, setting_value FROM site_default_view"))
        self._site_defaults = {row.setting_name: row.setting_value or "" for row in rows}
        self._site_defaults_read_at = now
        return self._site_defaults

    async def devices(self, session: AsyncSession, user_ids: Sequence[int]) -> dict[int, list[Device]]:
        """Enabled devices, grouped by user."""
        if not user_ids:
            return {}

        rows = await session.execute(
            text(
                "SELECT device_id, user_id, app_id, platform, push_token, enabled "
                "  FROM device_view "
                " WHERE user_id IN :ids AND enabled = 1"
            ).bindparams(ids=tuple(user_ids)),
        )

        found_devices: dict[int, list[Device]] = {}
        for row in rows:
            found_devices.setdefault(row.user_id, []).append(
                Device(
                    device_id=row.device_id,
                    user_id=row.user_id,
                    app_id=row.app_id or "",
                    platform=row.platform or "",
                    push_token=row.push_token or "",
                    enabled=int(row.enabled or 0),
                )
            )
        return found_devices
