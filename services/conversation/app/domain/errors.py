"""Domain errors."""


class DomainError(Exception):
    """Root of every domain error."""


class ConversationNotFound(DomainError):
    """The requested conversation does not exist."""

    def __init__(self, conversation_id: int) -> None:
        super().__init__(f"Conversation {conversation_id} does not exist")
        self.conversation_id = conversation_id


class MessageNotFound(DomainError):
    """The requested message does not exist."""

    def __init__(self, message_id: int) -> None:
        super().__init__(f"Message {message_id} does not exist")
        self.message_id = message_id


class InvalidConversation(DomainError):
    """The requested composition violates the rules of the aggregate."""


class NotAMember(DomainError):
    """The user is not a member of the conversation."""

    def __init__(self, user_id: int, conversation_id: int) -> None:
        super().__init__(f"User {user_id} is not a member of conversation {conversation_id}")
        self.user_id = user_id
        self.conversation_id = conversation_id


class MessageTooLong(DomainError):
    """The message exceeds the allowed length."""
