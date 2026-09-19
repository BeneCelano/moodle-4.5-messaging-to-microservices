"""The push channel, towards Airnotifier."""

from __future__ import annotations

import logging

import httpx

from ...domain.channels import Recipient
from .base import Content, DeliveryFailure, PermanentFailure

logger = logging.getLogger(__name__)


class AirnotifierProcessor:
    """Delivery to the registered devices."""

    name = "airnotifier"

    def __init__(
        self,
        *,
        base_url: str,
        app_id: str,
        token: str,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._app_id = app_id
        self._token = token
        self._timeout = timeout_seconds

    def is_user_configured(self, recipient: Recipient, context: dict) -> bool:
        return bool(context.get("devices"))

    async def send(self, recipient: Recipient, content: Content, context: dict) -> None:
        """Send the notification to every enabled device of the recipient."""
        devices = context.get("devices") or []
        if not devices:
            raise PermanentFailure(f"No registered device for user {recipient.user_id}")

        headers = {
            "X-AN-APP-NAME": self._app_id,
            "X-AN-APP-KEY": self._token,
            "Content-Type": "application/json",
        }

        succeeded = 0
        last_error: str = ""
        temporary = False

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for device in devices:
                body = {
                    "deviceid": device.push_token,
                    "device": device.platform,
                    "title": content.subject,
                    "alert": content.small_message,
                    "extra": content.presentation,
                }
                try:
                    response = await client.post(
                        f"{self._base_url}/api/v2/push/", json=body, headers=headers
                    )
                    if response.status_code < 400:
                        succeeded += 1
                        continue
                    last_error = f"HTTP {response.status_code}"
                    # Client errors are permanent, server errors retryable.
                    temporary = temporary or response.status_code >= 500
                except (httpx.HTTPError, OSError, TimeoutError) as error:
                    last_error = str(error)
                    temporary = True

        if succeeded:
            return
        if temporary:
            raise DeliveryFailure(last_error or "No device accepted the notification")
        raise PermanentFailure(last_error or "No device accepted the notification")
