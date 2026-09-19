"""Use cases of the notification context."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..domain.constants import SORT_DESC
from ..domain.entities import Notification, NotificationTemplate
from ..domain.errors import NotificationNotFound
from ..domain.events import notification_created
from ..domain.ports import Clock, NotificationMetrics, UnitOfWork

logger = logging.getLogger(__name__)


class NotificationService:
    """Write, read and clean up notifications."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        metrics: NotificationMetrics | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        # Optional: tests build the service without counters.
        self._metrics = metrics

    # -- writes ------------------------------------------------------------------------------

    async def send(
        self,
        *,
        user_id_from: int,
        user_id_to: int,
        component: str,
        event_type: str,
        subject: str = "",
        full_message: str = "",
        full_message_format: int = 0,
        full_message_html: str = "",
        small_message: str = "",
        context_url: str | None = None,
        context_url_name: str | None = None,
        custom_data: str | None = None,
        in_popup: bool = True,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Write a notification and announce the fact."""
        now = self._clock.now()

        async with self._uow_factory() as uow:
            if idempotency_key:
                already_done = await uow.commands.find_response(idempotency_key, "send_notification")
                if already_done is not None:
                    return already_done

            notification = Notification(
                user_id_from=user_id_from,
                user_id_to=user_id_to,
                component=component,
                event_type=event_type,
                subject=subject,
                full_message=full_message,
                full_message_format=full_message_format,
                full_message_html=full_message_html,
                small_message=small_message,
                context_url=context_url,
                context_url_name=context_url_name,
                custom_data=custom_data,
                time_created=now,
            )

            written = await uow.notifications.create(notification, in_popup=in_popup)

            uow.events.collect(
                notification_created(
                    notification_id=written.id,
                    user_id_from=written.user_id_from,
                    user_id_to=written.user_id_to,
                    component=component,
                    event_type=event_type,
                    subject=written.subject,
                    full_message=written.full_message,
                    full_message_format=written.full_message_format,
                    full_message_html=written.full_message_html,
                    small_message=written.small_message,
                    context_url=written.context_url,
                    context_url_name=written.context_url_name,
                    time_created=written.time_created,
                    correlation_id=correlation_id,
                )
            )

            response = {"id": written.id, "time_created": written.time_created, "in_popup": in_popup}

            if idempotency_key:
                await uow.commands.record(idempotency_key, "send_notification", response, now)

            await uow.commit()

            if self._metrics is not None:
                self._metrics.persisted(component)

            return response

    async def mark_read(self, notification_id: int, time_read: int | None = None) -> dict:
        now = time_read if time_read is not None else self._clock.now()

        async with self._uow_factory() as uow:
            notification = await uow.notifications.get(notification_id)
            if notification is None:
                raise NotificationNotFound(notification_id)

            changed = await uow.notifications.mark_read(notification_id, now)
            await uow.commit()

            return {
                "id": notification_id,
                "time_read": now if changed else notification.time_read,
                "changed": changed,
            }

    async def mark_all_read(
        self,
        user_id_to: int,
        *,
        user_id_from: int | None = None,
        time_created_to: int | None = None,
    ) -> dict:
        """Mark all the unread notifications of the person as read."""
        now = self._clock.now()

        async with self._uow_factory() as uow:
            count = await uow.notifications.mark_all_read(
                user_id_to,
                time_read=now,
                user_id_from=user_id_from,
                time_created_to=time_created_to,
            )
            await uow.commit()
            return {"marked": count, "time_read": now}

    # -- reads -------------------------------------------------------------------------------

    async def get(self, notification_id: int) -> Notification:
        """Return a notification."""
        async with self._uow_factory() as uow:
            notification = await uow.notifications.get(notification_id)
            if notification is None:
                raise NotificationNotFound(notification_id)
            return notification

    async def list_for_user(
        self,
        user_id_to: int | None,
        *,
        user_id_from: int | None = None,
        read: bool | None = None,
        sort: str = SORT_DESC,
        limit_from: int = 0,
        limit_num: int = 0,
    ) -> list[Notification]:
        """List the notifications received by a person, or sent by a person to anyone.
        """
        async with self._uow_factory() as uow:
            return await uow.notifications.list_for_user(
                user_id_to,
                user_id_from=user_id_from,
                read=read,
                sort=sort,
                limit_from=limit_from,
                limit_num=limit_num,
            )

    async def count_unread(self, user_id_to: int, *, user_id_from: int | None = None) -> int:
        """Count the unread notifications received by a person."""
        async with self._uow_factory() as uow:
            return await uow.notifications.count_unread(user_id_to, user_id_from=user_id_from)

    async def list_in_popup(
        self,
        user_id_to: int,
        *,
        sort: str = SORT_DESC,
        limit_from: int = 0,
        limit_num: int = 0,
    ) -> list[Notification]:
        """List the notifications shown in the popup."""
        async with self._uow_factory() as uow:
            return await uow.notifications.list_in_popup(
                user_id_to, sort=sort, limit_from=limit_from, limit_num=limit_num
            )

    async def count_unread_in_popup(self, user_id_to: int) -> int:
        async with self._uow_factory() as uow:
            return await uow.notifications.count_unread_in_popup(user_id_to)

    async def exists_with_custom_data(
        self,
        user_id_to: int,
        custom_data: str,
        *,
        component: str | None = None,
        event_type: str | None = None,
    ) -> bool:
        """Whether that person already received a notification with that custom data."""
        async with self._uow_factory() as uow:
            return await uow.notifications.exists_with_custom_data(
                user_id_to, custom_data, component=component, event_type=event_type
            )

    # -- personal data -----------------------------------------------------------------------
    #
    # Export, existence and erasure look at a person in both directions, received and sent.

    async def has_personal_data(self, user_id: int) -> bool:
        """Whether the site keeps notifications about this person."""
        async with self._uow_factory() as uow:
            return await uow.notifications.exists_involving(user_id)

    async def export_personal_data(self, user_id: int) -> list[Notification]:
        """Return every notification about this person, oldest first."""
        async with self._uow_factory() as uow:
            return await uow.notifications.list_involving(user_id)

    async def erase_personal_data(self, user_id: int) -> int:
        """Remove every notification about this person."""
        async with self._uow_factory() as uow:
            count = await uow.notifications.delete_involving(user_id)
            await uow.commit()
            return count

    # -- cleanup -----------------------------------------------------------------------------

    async def delete(self, notification_id: int) -> bool:
        """Remove a notification."""
        async with self._uow_factory() as uow:
            removed = await uow.notifications.delete(notification_id)
            await uow.commit()
            return removed

    async def delete_created_before(self, timestamp: int) -> int:
        """Delete the notifications older than the given instant."""
        async with self._uow_factory() as uow:
            count = await uow.notifications.delete_created_before(timestamp)
            await uow.commit()
            return count

    async def delete_read_before(self, timestamp: int) -> int:
        async with self._uow_factory() as uow:
            count = await uow.notifications.delete_read_before(timestamp)
            await uow.commit()
            return count

    # -- templates ---------------------------------------------------------------------------

    async def seed_templates(self, templates: list[dict]) -> dict:
        """Register the templates with which the service composes the notifications it
        produces itself.
        """
        now = self._clock.now()

        async with self._uow_factory() as uow:
            for data in templates:
                await uow.templates.upsert(
                    NotificationTemplate(
                        component=data["component"],
                        event_type=data["event_type"],
                        lang=data["lang"],
                        subject=data["subject"],
                        body=data["body"],
                        custom_data=data.get("custom_data"),
                    ),
                    now,
                )

            languages = sorted(await uow.templates.languages())
            await uow.commit()

            return {"written": len(templates), "languages": languages}
