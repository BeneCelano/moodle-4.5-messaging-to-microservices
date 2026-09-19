"""Values of the conversation domain."""

from enum import IntEnum


class ConversationType(IntEnum):
    """Conversation type."""

    INDIVIDUAL = 1
    GROUP = 2
    SELF = 3


class ConversationState(IntEnum):
    """Enabled state of a conversation."""

    DISABLED = 0
    ENABLED = 1


class MessageAction(IntEnum):
    """Action recorded by a user on a single message."""

    READ = 1
    DELETED = 2


class ConversationAction(IntEnum):
    """Action recorded by a user on a whole conversation."""

    MUTED = 1


MESSAGE_MAX_LENGTH = 4096
"""Maximum length of a message, as in the monolith."""
