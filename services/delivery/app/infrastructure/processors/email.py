"""The mail channel: what `message_output_email::send_message()` and `email_to_user()`
do.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from ...domain.channels import Recipient
from .base import Content, DeliveryFailure, PermanentFailure

logger = logging.getLogger(__name__)

FORMAT_HTML = 1


class EmailProcessor:
    """Delivery by email."""

    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        timeout_seconds: float,
        from_address: str,
        from_name: str,
        allow_address_override: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds
        self._from_address = from_address
        self._from_name = from_name
        self._allow_override = allow_address_override

    def is_user_configured(self, recipient: Recipient, context: dict) -> bool:
        return bool(self._address_for(recipient, context))

    def _address_for(self, recipient: Recipient, context: dict) -> str:
        """Destination address, honouring the override."""
        if self._allow_override:
            override = (context.get("preferences") or {}).get("message_processor_email_email")
            if override:
                return override
        return recipient.email or ""

    async def send(self, recipient: Recipient, content: Content, context: dict) -> None:
        """Compose and send the message."""
        address = self._address_for(recipient, context)
        if not address:
            raise PermanentFailure(f"No address for user {recipient.user_id}")

        message = EmailMessage()
        message["From"] = f"{self._from_name} <{self._from_address}>"
        message["To"] = f"{recipient.full_name} <{address}>"
        message["Subject"] = content.subject

        # The recipient chooses the form of the body (`mailformat`); without HTML the
        # plain text is used.
        if recipient.mail_format == FORMAT_HTML and content.full_message_html:
            message.set_content(content.full_message)
            message.add_alternative(content.full_message_html, subtype="html")
        else:
            message.set_content(content.full_message)

        try:
            await aiosmtplib.send(
                message,
                hostname=self._host,
                port=self._port,
                timeout=self._timeout,
            )
        except aiosmtplib.SMTPRecipientsRefused as error:
            # The server refused the recipient: permanent.
            raise PermanentFailure(str(error)) from error
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as error:
            # Provider or network fault: retryable.
            raise DeliveryFailure(str(error)) from error
