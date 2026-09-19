"""The common contract of the outbound processors, `message_output` of the monolith
reduced to what is needed: whether a processor is usable for a recipient, and
deliver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...domain.channels import Recipient


class DeliveryFailure(Exception):
    """The delivery failed and retrying makes sense: faults of the external provider."""


class PermanentFailure(Exception):
    """The delivery will never succeed: a malformed address, a device no longer
    registered.
    """


@dataclass(frozen=True, slots=True)
class Content:
    """The content to deliver, already composed."""

    subject: str
    full_message: str
    full_message_html: str
    small_message: str
    context_url: str | None
    context_url_name: str | None
    presentation: dict

    @classmethod
    def from_payload(cls, payload: dict) -> Content:
        """Extract the content from the event envelope."""
        text = payload.get("full_message") or payload.get("text") or ""
        return cls(
            subject=payload.get("subject") or "",
            full_message=text,
            full_message_html=payload.get("full_message_html") or "",
            small_message=payload.get("small_message") or text,
            context_url=payload.get("context_url"),
            context_url_name=payload.get("context_url_name"),
            presentation=payload.get("presentation") or {},
        )


class Processor(Protocol):
    """An outbound channel."""

    name: str

    def is_user_configured(self, recipient: Recipient, context: dict) -> bool:
        """Whether the processor is usable for that recipient."""
        ...

    async def send(self, recipient: Recipient, content: Content, context: dict) -> None:
        """Deliver, or raise `DeliveryFailure` or `PermanentFailure`."""
        ...
