"""Access to the service schema."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.constants import SORT_ASC, SORT_DESC
from ..domain.entities import Notification, NotificationTemplate
from .models import (
    NotificationRow,
    NotificationTemplateRow,
    PopupNotificationRow,
    ProcessedCommandRow,
    ProcessedEventRow,
)


def _to_entity(row: NotificationRow) -> Notification:
    return Notification(
        id=row.id,
        user_id_from=row.user_id_from,
        user_id_to=row.user_id_to,
        # Without `or ""`: the column is nullable, and NULL is returned as such.
        subject=row.subject,
        full_message=row.full_message,
        full_message_format=row.full_message_format,
        full_message_html=row.full_message_html,
        small_message=row.small_message,
        component=row.component,
        event_type=row.event_type,
        context_url=row.context_url,
        context_url_name=row.context_url_name,
        custom_data=row.custom_data,
        time_created=row.time_created,
        time_read=row.time_read,
    )


class SqlNotificationRepository:
    """The notifications and their visibility in the popup."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- writes ------------------------------------------------------------------------------

    async def create(self, notification: Notification, *, in_popup: bool) -> Notification:
        """Write the notification and, if asked, make it visible in the popup."""
        row = NotificationRow(
            user_id_from=notification.user_id_from,
            user_id_to=notification.user_id_to,
            subject=notification.subject,
            full_message=notification.full_message,
            full_message_format=notification.full_message_format,
            full_message_html=notification.full_message_html,
            small_message=notification.small_message,
            component=notification.component,
            event_type=notification.event_type,
            context_url=notification.context_url,
            context_url_name=notification.context_url_name,
            time_read=notification.time_read,
            time_created=notification.time_created,
            custom_data=notification.custom_data,
        )
        self._session.add(row)
        await self._session.flush()

        if in_popup:
            self._session.add(PopupNotificationRow(notification_id=row.id))
            await self._session.flush()

        return _to_entity(row)

    async def mark_read(self, notification_id: int, time_read: int) -> bool:
        """Mark a notification as read and say whether the state changed."""
        result = await self._session.execute(
            update(NotificationRow)
            .where(NotificationRow.id == notification_id, NotificationRow.time_read.is_(None))
            .values(time_read=time_read)
        )
        return bool(result.rowcount)

    async def mark_all_read(
        self,
        user_id_to: int,
        *,
        time_read: int,
        user_id_from: int | None = None,
        time_created_to: int | None = None,
    ) -> int:
        """Mark all the unread notifications of the person as read and return how many.
        """
        statement = update(NotificationRow).where(
            NotificationRow.user_id_to == user_id_to, NotificationRow.time_read.is_(None)
        )
        if user_id_from is not None:
            statement = statement.where(NotificationRow.user_id_from == user_id_from)
        if time_created_to is not None:
            statement = statement.where(NotificationRow.time_created <= time_created_to)

        result = await self._session.execute(statement.values(time_read=time_read))
        return int(result.rowcount or 0)

    # -- reads -------------------------------------------------------------------------------

    async def get(self, notification_id: int) -> Notification | None:
        """Return a notification, or None if it does not exist."""
        row = await self._session.get(NotificationRow, notification_id)
        return _to_entity(row) if row is not None else None

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
        """List the notifications received by a person, or those sent by a person to
        anyone.
        """
        if user_id_to is None and user_id_from is None:
            raise ValueError("A read must name at least the recipient or the sender.")

        statement = select(NotificationRow)

        if user_id_to is not None:
            statement = statement.where(NotificationRow.user_id_to == user_id_to)
        if user_id_from is not None:
            statement = statement.where(NotificationRow.user_id_from == user_id_from)
        if read is True:
            statement = statement.where(NotificationRow.time_read.is_not(None))
        elif read is False:
            statement = statement.where(NotificationRow.time_read.is_(None))

        statement = self._ordered(statement, sort)
        statement = self._windowed(statement, limit_from, limit_num)

        result = await self._session.execute(statement)
        return [_to_entity(row) for row in result.scalars().all()]

    async def count_unread(self, user_id_to: int, *, user_id_from: int | None = None) -> int:
        """Count the unread notifications received by a person."""
        statement = select(func.count(NotificationRow.id)).where(
            NotificationRow.user_id_to == user_id_to, NotificationRow.time_read.is_(None)
        )
        if user_id_from is not None:
            statement = statement.where(NotificationRow.user_id_from == user_id_from)

        result = await self._session.execute(statement)
        return int(result.scalar() or 0)

    async def list_in_popup(
        self,
        user_id_to: int,
        *,
        sort: str = SORT_DESC,
        limit_from: int = 0,
        limit_num: int = 0,
    ) -> list[Notification]:
        """List the notifications shown in the popup."""
        statement = (
            select(NotificationRow)
            .join(PopupNotificationRow, PopupNotificationRow.notification_id == NotificationRow.id)
            .where(NotificationRow.user_id_to == user_id_to)
        )
        statement = self._ordered(statement, sort)
        statement = self._windowed(statement, limit_from, limit_num)

        result = await self._session.execute(statement)
        return [_to_entity(row) for row in result.scalars().all()]

    async def count_unread_in_popup(self, user_id_to: int) -> int:
        result = await self._session.execute(
            select(func.count(NotificationRow.id))
            .join(PopupNotificationRow, PopupNotificationRow.notification_id == NotificationRow.id)
            .where(NotificationRow.user_id_to == user_id_to, NotificationRow.time_read.is_(None))
        )
        return int(result.scalar() or 0)

    async def exists_with_custom_data(
        self,
        user_id_to: int,
        custom_data: str,
        *,
        component: str | None = None,
        event_type: str | None = None,
    ) -> bool:
        """Whether that person already received a notification with that custom data."""
        statement = select(NotificationRow.id).where(
            NotificationRow.user_id_to == user_id_to,
            func.substr(NotificationRow.custom_data, 1, 255) == custom_data[:255],
        )
        if component is not None:
            statement = statement.where(NotificationRow.component == component)
        if event_type is not None:
            statement = statement.where(NotificationRow.event_type == event_type)

        result = await self._session.execute(statement.limit(1))
        return result.scalars().first() is not None

    # -- personal data -----------------------------------------------------------------------
    #
    # The two ends combine with OR: received and sent.

    async def exists_involving(self, user_id: int) -> bool:
        """Whether the site keeps notifications about this person."""
        result = await self._session.execute(
            select(NotificationRow.id)
            .where(
                or_(
                    NotificationRow.user_id_to == user_id,
                    NotificationRow.user_id_from == user_id,
                )
            )
            .limit(1)
        )
        return result.scalars().first() is not None

    async def list_involving(self, user_id: int) -> list[Notification]:
        """List every notification about this person, oldest first."""
        result = await self._session.execute(
            select(NotificationRow)
            .where(
                or_(
                    NotificationRow.user_id_to == user_id,
                    NotificationRow.user_id_from == user_id,
                )
            )
            .order_by(NotificationRow.time_created.asc(), NotificationRow.id.asc())
        )
        return [_to_entity(row) for row in result.scalars().all()]

    async def delete_involving(self, user_id: int) -> int:
        """Remove every notification about this person and return how many."""
        result = await self._session.execute(
            delete(NotificationRow).where(
                or_(
                    NotificationRow.user_id_to == user_id,
                    NotificationRow.user_id_from == user_id,
                )
            )
        )
        return int(result.rowcount or 0)

    # -- cleanup -----------------------------------------------------------------------------

    async def delete(self, notification_id: int) -> bool:
        """Remove a notification and say whether it existed."""
        result = await self._session.execute(
            delete(NotificationRow).where(NotificationRow.id == notification_id)
        )
        return bool(result.rowcount)

    async def delete_created_before(self, timestamp: int) -> int:
        """Delete the notifications older than the given instant."""
        result = await self._session.execute(
            delete(NotificationRow).where(NotificationRow.time_created < timestamp)
        )
        return int(result.rowcount or 0)

    async def delete_read_before(self, timestamp: int) -> int:
        result = await self._session.execute(
            delete(NotificationRow).where(
                NotificationRow.time_read.is_not(None), NotificationRow.time_read < timestamp
            )
        )
        return int(result.rowcount or 0)

    # -- helpers -----------------------------------------------------------------------------

    @staticmethod
    def _ordered(statement, sort: str):
        """Apply the monolith ordering in the requested direction."""
        ascending = sort.upper() == SORT_ASC
        columns = (NotificationRow.time_created, NotificationRow.time_read, NotificationRow.id)
        return statement.order_by(*(c.asc() if ascending else c.desc() for c in columns))

    @staticmethod
    def _windowed(statement, limit_from: int, limit_num: int):
        """Apply the requested window."""
        if limit_num > 0:
            statement = statement.limit(limit_num)
        elif limit_from > 0:
            statement = statement.limit(2**31 - 1)
        if limit_from > 0:
            statement = statement.offset(limit_from)
        return statement


class SqlTemplateRepository:
    """The templates with which the service composes the notifications it produces
    itself.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(
        self, component: str, event_type: str, lang: str
    ) -> NotificationTemplate | None:
        """Return the template for that message in that language, if it exists."""
        result = await self._session.execute(
            select(NotificationTemplateRow).where(
                NotificationTemplateRow.component == component,
                NotificationTemplateRow.event_type == event_type,
                NotificationTemplateRow.lang == lang,
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        return NotificationTemplate(
            component=row.component,
            event_type=row.event_type,
            lang=row.lang,
            subject=row.subject,
            body=row.body,
            custom_data=row.custom_data,
        )

    async def upsert(self, template: NotificationTemplate, now: int) -> None:
        """Register or update a template."""
        result = await self._session.execute(
            select(NotificationTemplateRow).where(
                NotificationTemplateRow.component == template.component,
                NotificationTemplateRow.event_type == template.event_type,
                NotificationTemplateRow.lang == template.lang,
            )
        )
        row = result.scalars().first()

        if row is None:
            self._session.add(
                NotificationTemplateRow(
                    component=template.component,
                    event_type=template.event_type,
                    lang=template.lang,
                    subject=template.subject,
                    body=template.body,
                    custom_data=template.custom_data,
                    updated_at=now,
                )
            )
        else:
            row.subject = template.subject
            row.body = template.body
            row.custom_data = template.custom_data
            row.updated_at = now

        await self._session.flush()

    async def languages(self) -> Sequence[str]:
        """List the languages for which at least one template exists."""
        result = await self._session.execute(select(NotificationTemplateRow.lang).distinct())
        return list(result.scalars().all())


class SqlProcessedEventRepository:
    """Register of the events already consumed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def result_of(self, event_id: str) -> dict | None:
        """Return the result produced the first time, if the event was already consumed.
        """
        result = await self._session.execute(
            select(ProcessedEventRow.result).where(ProcessedEventRow.event_id == event_id)
        )
        row = result.scalars().first()
        return dict(row) if row is not None else None

    async def remember(self, event_id: str, event_type: str, result: dict, now: int) -> None:
        """Record the consumption of an event and the result produced."""
        self._session.add(
            ProcessedEventRow(
                event_id=event_id, event_type=event_type, result=result, created_at=now
            )
        )
        await self._session.flush()


class SqlCommandRepository:
    """Register of the synchronous commands already executed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_response(self, idempotency_key: str, endpoint: str) -> dict | None:
        """Return the answer already produced for that command, if any."""
        result = await self._session.execute(
            select(ProcessedCommandRow.response).where(
                ProcessedCommandRow.idempotency_key == idempotency_key,
                ProcessedCommandRow.endpoint == endpoint,
            )
        )
        row = result.scalars().first()
        return dict(row) if row is not None else None

    async def record(self, idempotency_key: str, endpoint: str, response: dict, now: int) -> None:
        """Record the answer produced for a command."""
        self._session.add(
            ProcessedCommandRow(
                idempotency_key=idempotency_key, endpoint=endpoint, response=response, created_at=now
            )
        )
        await self._session.flush()


__all__ = [
    "SqlCommandRepository",
    "SqlNotificationRepository",
    "SqlProcessedEventRepository",
    "SqlTemplateRepository",
]
