"""Representations of the REST contract, written in the domain vocabulary (`user_id_to`,
not `useridto`): the translation is done by the PHP client, so the service is not
bound to the legacy schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.constants import FORMAT_MOODLE


class NotificationOut(BaseModel):
    """Notification returned to the caller."""

    id: int
    user_id_from: int
    user_id_to: int
    # Nullable as in the monolith.
    subject: str | None = None
    full_message: str | None = None
    full_message_format: int = FORMAT_MOODLE
    full_message_html: str | None = None
    small_message: str | None = None
    component: str | None = None
    event_type: str | None = None
    context_url: str | None = None
    context_url_name: str | None = None
    custom_data: str | None = None
    time_created: int
    time_read: int | None = None


class NotificationListOut(BaseModel):
    """List of notifications."""

    notifications: list[NotificationOut]


class SendNotificationIn(BaseModel):
    """Request to write a notification."""

    user_id_from: int
    user_id_to: int
    # component and event_type are required: templates and modules look the notification
    # up by them.
    component: str
    event_type: str
    subject: str | None = None
    full_message: str | None = None
    full_message_format: int = FORMAT_MOODLE
    full_message_html: str | None = None
    small_message: str | None = None
    context_url: str | None = None
    context_url_name: str | None = None
    custom_data: str | None = None
    # Passed by the caller that resolves the channels; see `NotificationService.send`.
    in_popup: bool = True


class SendNotificationOut(BaseModel):
    """Outcome of writing a notification."""

    id: int
    time_created: int
    in_popup: bool


class MarkReadOut(BaseModel):
    """Outcome of marking a notification as read."""

    id: int
    time_read: int | None
    changed: bool


class MarkAllReadOut(BaseModel):
    """Outcome of marking all the notifications of a person as read."""

    marked: int
    time_read: int


class CountOut(BaseModel):
    """A count."""

    count: int


class ExistsOut(BaseModel):
    """Whether a notification with that custom data exists."""

    exists: bool


class CleanupIn(BaseModel):
    """Request for the periodic cleanup."""

    created_before: int = 0
    read_before: int = 0


class CleanupOut(BaseModel):
    """Outcome of the periodic cleanup."""

    deleted_by_age: int
    deleted_by_read: int


class HealthOut(BaseModel):
    """State of the service."""

    status: str
    service: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)



class TemplateIn(BaseModel):
    """A notification template, as the monolith seeds it."""

    component: str
    event_type: str
    lang: str
    subject: str
    body: str
    custom_data: str | None = None


class SeedTemplatesIn(BaseModel):
    """The set of templates to register."""

    templates: list[TemplateIn]


class SeedTemplatesOut(BaseModel):
    """Outcome of the seed."""

    written: int
    languages: list[str]


class ErasureOut(BaseModel):
    """Outcome of the erasure of personal data."""

    deleted: int
