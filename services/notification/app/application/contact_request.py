"""Consumption of the event with which the ConversationService announces a contact
request.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from ..domain.constants import FORMAT_HTML
from ..domain.entities import Notification
from ..domain.errors import UnknownTemplate
from ..domain.events import CONTACT_REQUEST_CREATED, notification_created
from ..domain.ports import Clock, NotificationMetrics, SiteFacts, UnitOfWork
from ..infrastructure.consumer import MalformedEvent
from .rendering import compose, full_name

logger = logging.getLogger(__name__)

COMPONENT = "moodle"
"""The component the monolith attributes this notification to."""

EVENT_TYPE = "messagecontactrequests"
"""The name the monolith gives this message, and the key of the channel preference."""

CONTACT_REQUESTS_PATH = "/message/index.php?view=contactrequests"
"""The page on which a contact request is answered."""

FALLBACK_LANGUAGE = "en"
"""Language used when the recipient has one for which no template exists."""


class ContactRequestHandler:
    """Turn the announcement of a contact request into the notification that
    communicates it.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        site: SiteFacts,
        metrics: NotificationMetrics | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._site = site
        # Optional: tests build the handler without counters.
        self._metrics = metrics

    async def handle(self, envelope: dict) -> None:
        """Consume the envelope and write the notification."""
        event_id = envelope.get("event_id")
        event_type = envelope.get("event_type")
        payload = envelope.get("payload") or {}

        if not event_id or not event_type:
            raise MalformedEvent("Envelope without event_id or event_type")

        if event_type != CONTACT_REQUEST_CREATED:
            # Other event types on the same queue are ignored, not rejected.
            logger.info("Event %s of type %s ignored by this handler", event_id, event_type)
            return

        user_id = payload.get("user_id")
        requested_user_id = payload.get("requested_user_id")

        if not isinstance(user_id, int) or not isinstance(requested_user_id, int):
            raise MalformedEvent(
                f"Event {event_id} does not say who asked for the contact and whom: "
                f"without the two identifiers the notification cannot be composed."
            )

        recipients = await self._site.recipients([requested_user_id, user_id])
        recipient = recipients.get(requested_user_id)
        requester = recipients.get(user_id)

        if recipient is None or requester is None:
            # One of the two users no longer exists: the event is still marked as
            # consumed.
            logger.info(
                "Contact request %s without recipient or requester, no notification",
                event_id,
            )
            await self._mark_consumed(event_id, event_type, {"skipped": "utenza inesistente"})
            return

        if not recipient.wants_notifications:
            logger.info("Recipient %s does not receive notifications", requested_user_id)
            await self._mark_consumed(event_id, event_type, {"skipped": "notifications disabled"})
            return

        settings = await self._site.site_settings()
        now = self._clock.now()

        async with self._uow_factory() as uow:
            already_done = await uow.processed_events.result_of(event_id)
            if already_done is not None:
                logger.info("Event %s already consumed, nothing to do", event_id)
                return

            template_row = await uow.templates.find(COMPONENT, EVENT_TYPE, recipient.lang)
            if template_row is None:
                template_row = await uow.templates.find(COMPONENT, EVENT_TYPE, FALLBACK_LANGUAGE)
            if template_row is None:
                # Without a template the event goes back to the queue.
                raise UnknownTemplate(COMPONENT, EVENT_TYPE, recipient.lang)

            values = {
                "user": full_name(requester, settings.get("fullnamedisplay")),
                "sitename": settings.get("sitename", ""),
                "url": settings.get("wwwroot", "") + CONTACT_REQUESTS_PATH,
            }

            html_body = compose(template_row.body, values)

            notification = Notification(
                user_id_from=user_id,
                user_id_to=requested_user_id,
                component=COMPONENT,
                event_type=EVENT_TYPE,
                subject=compose(template_row.subject, values),
                # The plain field carries the text without markup.
                full_message=_without_markup(html_body),
                full_message_format=FORMAT_HTML,
                full_message_html=html_body,
                small_message="",
                context_url=values["url"],
                custom_data=self._custom_data(template_row.custom_data, values, settings, user_id),
                time_created=now,
            )

            # Contact requests always appear in the popup.
            written = await uow.notifications.create(notification, in_popup=True)

            uow.events.collect(
                notification_created(
                    notification_id=written.id,
                    user_id_from=written.user_id_from,
                    user_id_to=written.user_id_to,
                    component=COMPONENT,
                    event_type=EVENT_TYPE,
                    subject=written.subject,
                    full_message=written.full_message,
                    full_message_format=written.full_message_format,
                    full_message_html=written.full_message_html,
                    small_message=written.small_message,
                    context_url=written.context_url,
                    context_url_name=written.context_url_name,
                    time_created=written.time_created,
                    correlation_id=envelope.get("correlation_id"),
                )
            )

            await uow.processed_events.remember(
                event_id, event_type, {"notification_id": written.id}, now
            )
            await uow.commit()

            if self._metrics is not None:
                self._metrics.persisted(COMPONENT)

            logger.info(
                "Contact request %s notified to user %s with notification %s",
                event_id,
                requested_user_id,
                written.id,
            )

    async def _mark_consumed(self, event_id: str, event_type: str, result: dict) -> None:
        """Record the consumption of an event that produced no notification."""
        async with self._uow_factory() as uow:
            if await uow.processed_events.result_of(event_id) is not None:
                return
            await uow.processed_events.remember(event_id, event_type, result, self._clock.now())
            await uow.commit()

    def _custom_data(
        self,
        template: str | None,
        values: dict[str, str],
        settings: dict[str, str],
        user_id: int,
    ) -> str | None:
        """Compose the custom data of the notification, which the interface uses to draw it: the labels of the
        two buttons (translated text, hence from the template) and the requester's profile picture URL.
        """
        if not template:
            return None

        try:
            labels = json.loads(template)
        except ValueError:
            logger.warning("Custom data template unreadable, the notification will do without it")
            return None

        wwwroot = settings.get("wwwroot", "")
        data = {
            "notificationiconurl": f"{wwwroot}/user/pix.php/{user_id}/f1.jpg" if wwwroot else "",
            "actionbuttons": {
                key: compose(str(text), values) for key, text in labels.items()
            },
        }
        return json.dumps(data, ensure_ascii=False)


def _without_markup(html: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()
