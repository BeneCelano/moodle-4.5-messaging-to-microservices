"""Routes of messages and of the sending rules."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ...core.observability import MESSAGES_PERSISTED
from ...domain.constants import ConversationType
from ..deps import (
    AuthDep,
    ConversationServiceDep,
    CorrelationDep,
    IdempotencyDep,
    MessageServiceDep,
)
from ..schemas import (
    ActionOut,
    ConversationMessagesOut,
    ConversationOut,
    ConversationSummaryListOut,
    ConversationSummaryOut,
    ConversationTypeListOut,
    ConversationTypeOut,
    CountOut,
    DeletionsOut,
    LastMessageOut,
    MarkAllReadIn,
    MarkedOut,
    MarkReadIn,
    MessageOut,
    MessageReferenceOut,
    MessageSearchOut,
    MessageSearchResultOut,
    SendInstantMessageIn,
    SendMessageIn,
)

router = APIRouter(prefix="/api/v1", tags=["messages"])


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: int,
    payload: SendMessageIn,
    messages: MessageServiceDep,
    conversations: ConversationServiceDep,
    correlation_id: CorrelationDep = None,
    idempotency_key: IdempotencyDep = None,
    _: AuthDep = None,
) -> MessageOut:
    """Persist a message and record the event announcing it."""
    result = await messages.send(
        conversation_id=conversation_id,
        user_id_from=payload.user_id_from,
        text=payload.text,
        message_format=payload.format,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )

    conversation = await conversations.get(conversation_id)
    MESSAGES_PERSISTED.labels(conversation_type=str(int(conversation.type))).inc()

    return MessageOut(**result)


@router.post("/messages/instant", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_instant_message(
    payload: SendInstantMessageIn,
    messages: MessageServiceDep,
    conversations: ConversationServiceDep,
    correlation_id: CorrelationDep = None,
    idempotency_key: IdempotencyDep = None,
    _: AuthDep = None,
) -> MessageOut:
    """Persist a message addressed to a user, resolving or creating the individual
    conversation.
    """
    conversation = await conversations.create(
        conversation_type=int(ConversationType.INDIVIDUAL),
        user_ids=[payload.user_id_from, payload.user_id_to],
    )

    result = await messages.send(
        conversation_id=int(conversation.id),
        user_id_from=payload.user_id_from,
        text=payload.text,
        message_format=payload.format,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )

    MESSAGES_PERSISTED.labels(conversation_type=str(int(conversation.type))).inc()

    return MessageOut(**result)


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesOut)
async def list_messages(
    conversation_id: int,
    messages: MessageServiceDep,
    user_id: int = Query(),
    time_deleted: int = Query(default=0, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=0, ge=0, le=1000),
    sort: str = Query(default="timecreated ASC"),
    time_from: int = Query(default=0, ge=0),
    time_to: int = Query(default=0, ge=0),
    _: AuthDep = None,
) -> ConversationMessagesOut:
    """Return the messages of the conversation visible to the user."""
    found = await messages.list_conversation_messages(
        user_id=user_id,
        conversation_id=conversation_id,
        time_deleted=time_deleted,
        offset=offset,
        limit=limit,
        descending="desc" in sort.lower(),
        time_from=time_from,
        time_to=time_to,
    )
    return ConversationMessagesOut(messages=found)


@router.delete("/conversations/{conversation_id}/messages", response_model=DeletionsOut)
async def delete_conversation_messages(
    conversation_id: int,
    messages: MessageServiceDep,
    user_id: int = Query(),
    _: AuthDep = None,
) -> DeletionsOut:
    """Record the deletion of every message of the conversation for a user."""
    return DeletionsOut(deleted=await messages.delete_conversation_for_user(conversation_id, user_id))


@router.get("/messages/{message_id}", response_model=MessageReferenceOut)
async def get_message(
    message_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> MessageReferenceOut:
    """Return a message from its identifier."""
    message = await messages.get_message(message_id)
    return MessageReferenceOut(
        id=int(message.id),
        conversation_id=message.conversation_id,
        user_id_from=message.user_id_from,
        time_created=message.time_created,
        small_message=message.small_message,
        full_message_format=message.full_message_format,
        full_message_trust=message.full_message_trust,
    )


@router.post("/messages/{message_id}/read", response_model=ActionOut)
async def mark_message_as_read(
    message_id: int,
    payload: MarkReadIn,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> ActionOut:
    """Record the reading of a message."""
    return ActionOut(action_id=await messages.mark_as_read(message_id, payload.user_id, payload.time_read))


@router.delete("/messages/{message_id}", response_model=ActionOut)
async def delete_message(
    message_id: int,
    messages: MessageServiceDep,
    user_id: int = Query(),
    _: AuthDep = None,
) -> ActionOut:
    """Record the deletion of a message for a user."""
    return ActionOut(action_id=await messages.delete_for_user(message_id, user_id))


@router.delete("/messages/{message_id}/all", response_model=DeletionsOut)
async def delete_message_for_all(
    message_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> DeletionsOut:
    """Record the deletion of a message for every member of the conversation."""
    return DeletionsOut(deleted=await messages.delete_for_all_users(message_id))


@router.post("/users/{user_id}/messages/read", response_model=MarkedOut)
async def mark_all_as_read(
    user_id: int,
    payload: MarkAllReadIn,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> MarkedOut:
    """Record the reading of every received and still unread message."""
    return MarkedOut(marked=await messages.mark_all_as_read(user_id, payload.conversation_id))


@router.get("/users/{user_id}/conversations/unread-count", response_model=CountOut)
async def count_unread_conversations(
    user_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> CountOut:
    """Count the conversations with at least one unread message."""
    return CountOut(count=await messages.count_unread_conversations(user_id))


@router.get(
    "/users/{user_id}/conversations/{conversation_id}/unread-count", response_model=CountOut
)
async def count_unread_in_conversation(
    user_id: int,
    conversation_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> CountOut:
    """Count the unread messages of a conversation, excluding those sent by the user."""
    return CountOut(count=await messages.count_unread_in_conversation(user_id, conversation_id))


@router.get("/users/{user_id}/conversations/unread", response_model=ConversationTypeListOut)
async def list_unread_conversations(
    user_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> ConversationTypeListOut:
    """List the conversations with at least one unread message, each with its type."""
    rows = await messages.list_unread_conversations(user_id)

    return ConversationTypeListOut(
        conversations=[ConversationTypeOut(conversation_id=cid, type=ctype) for cid, ctype in rows]
    )


@router.get("/users/{user_id}/messages/search", response_model=MessageSearchOut)
async def search_messages(
    user_id: int,
    messages: MessageServiceDep,
    text: str = Query(),
    offset: int = Query(0, ge=0),
    limit: int = Query(0, ge=0),
    _: AuthDep = None,
) -> MessageSearchOut:
    """Search the messages the user sent or received, most recent first."""
    found = await messages.search(user_id, text, offset, limit)

    return MessageSearchOut(
        results=[
            MessageSearchResultOut(
                message_id=row.message_id,
                conversation_id=row.conversation_id,
                user_id_from=row.user_id_from,
                user_id_to=row.user_id_to,
                subject=row.subject,
                full_message=row.full_message,
                full_message_html=row.full_message_html,
                full_message_format=row.full_message_format,
                small_message=row.small_message,
                time_created=row.time_created,
                user_from_blocked=row.user_from_blocked,
                user_to_blocked=row.user_to_blocked,
            )
            for row in found
        ]
    )


@router.get("/messages/{message_id}/conversation", response_model=ConversationOut)
async def conversation_of_message(
    message_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> ConversationOut:
    """Return the conversation the message belongs to."""
    conversation = await messages.conversation_of(message_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    return ConversationOut(
        id=int(conversation.id),
        type=int(conversation.type),
        name=conversation.name,
        conversation_hash=conversation.conversation_hash,
        component=conversation.component,
        item_type=conversation.item_type,
        item_id=conversation.item_id,
        context_id=conversation.context_id,
        enabled=conversation.enabled,
        time_created=conversation.time_created,
        time_modified=conversation.time_modified,
        member_ids=conversation.member_ids(),
    )


@router.get("/users/{user_id}/conversations", response_model=ConversationSummaryListOut)
async def list_conversations(
    user_id: int,
    messages: MessageServiceDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(0, ge=0),
    conversation_type: int | None = Query(None),
    merge_self: bool = Query(False),
    favourite_ids: list[int] = Query([]),  # noqa: B008 - the documented FastAPI form for lists
    only_favourites: bool | None = Query(None),
    _: AuthDep = None,
) -> ConversationSummaryListOut:
    """Compose the list of a user's conversations, most recent first."""
    entries = await messages.list_conversations(
        user_id,
        offset=offset,
        limit=limit,
        conversation_type=conversation_type,
        merge_self=merge_self,
        favourite_ids=favourite_ids,
        only_favourites=only_favourites,
    )

    return ConversationSummaryListOut(
        conversations=[
            ConversationSummaryOut(
                id=entry.id,
                type=entry.type,
                name=entry.name,
                component=entry.component,
                item_type=entry.item_type,
                item_id=entry.item_id,
                context_id=entry.context_id,
                is_muted=entry.is_muted,
                member_count=entry.member_count,
                unread_count=entry.unread_count,
                member_ids=entry.member_ids,
                last_message=None
                if entry.last_message is None
                else LastMessageOut(
                    id=int(entry.last_message.id),
                    user_id_from=entry.last_message.user_id_from,
                    small_message=entry.last_message.small_message,
                    full_message=entry.last_message.full_message,
                    full_message_format=entry.last_message.full_message_format,
                    full_message_html=entry.last_message.full_message_html,
                    full_message_trust=entry.last_message.full_message_trust,
                    time_created=entry.last_message.time_created,
                ),
            )
            for entry in entries
        ]
    )


@router.get("/users/{user_id}/conversations/countable", response_model=ConversationTypeListOut)
async def list_countable_conversations(
    user_id: int,
    messages: MessageServiceDep,
    _: AuthDep = None,
) -> ConversationTypeListOut:
    """List the conversations of the user that must be counted, each with its type."""
    rows = await messages.list_countable_conversations(user_id)

    return ConversationTypeListOut(
        conversations=[ConversationTypeOut(conversation_id=cid, type=ctype) for cid, ctype in rows]
    )


