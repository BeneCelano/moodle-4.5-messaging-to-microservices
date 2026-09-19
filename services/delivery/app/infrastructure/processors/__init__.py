"""The outbound processors: `email` and `airnotifier`, the two extensions of Moodle's
`message_output` that deliver outside the application.
"""

from .airnotifier import AirnotifierProcessor
from .base import Content, DeliveryFailure, PermanentFailure, Processor
from .email import EmailProcessor

__all__ = [
    "AirnotifierProcessor",
    "Content",
    "DeliveryFailure",
    "EmailProcessor",
    "PermanentFailure",
    "Processor",
]
