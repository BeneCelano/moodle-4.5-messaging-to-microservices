"""The notification domain."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import FORMAT_MOODLE


@dataclass(slots=True)
class Notification:
    """A notification, as the domain knows it."""

    user_id_from: int
    user_id_to: int
    # Six nullable fields, as Moodle declares them: NULL and empty string are different
    # values.
    component: str | None = None
    event_type: str | None = None
    subject: str | None = None
    full_message: str | None = None
    full_message_format: int = FORMAT_MOODLE
    full_message_html: str | None = None
    small_message: str | None = None
    context_url: str | None = None
    context_url_name: str | None = None
    custom_data: str | None = None
    time_created: int = 0
    time_read: int | None = None
    id: int | None = None

    @property
    def is_read(self) -> bool:
        """Whether the notification is read."""
        return self.time_read is not None


@dataclass(frozen=True, slots=True)
class Recipient:
    """The facts about the recipient that this context does not own and reads from the
    view.
    """

    user_id: int
    first_name: str
    last_name: str
    lang: str
    email_stop: bool
    deleted: bool

    @property
    def wants_notifications(self) -> bool:
        """Whether the person receives notifications."""
        return not self.deleted and not self.email_stop


@dataclass(frozen=True, slots=True)
class NotificationTemplate:
    """Template with which the service composes the notifications born from an event."""

    component: str
    event_type: str
    lang: str
    subject: str
    body: str
    custom_data: str | None = None
