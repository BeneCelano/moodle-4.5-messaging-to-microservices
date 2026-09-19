"""Domain errors."""

from __future__ import annotations


class NotificationNotFound(Exception):
    """The requested notification does not exist."""

    def __init__(self, notification_id: int) -> None:
        super().__init__(f"Notification {notification_id} does not exist.")
        self.notification_id = notification_id


class InvalidNotification(Exception):
    """The proposed notification cannot be written as it is."""


class UnknownTemplate(Exception):
    """No template exists for the requested message."""

    def __init__(self, component: str, event_type: str, lang: str) -> None:
        super().__init__(
            f"No template for {component}/{event_type} in language '{lang}', "
            f"nor in the fallback language."
        )
        self.component = component
        self.event_type = event_type
        self.lang = lang
