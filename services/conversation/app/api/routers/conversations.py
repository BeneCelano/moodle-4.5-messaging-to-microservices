"""Routes of the conversation aggregate."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from ...domain.entities import Conversation
from ..deps import AuthDep, ConversationServiceDep
from ..schemas import (
    ConversationListOut,
    ConversationOut,
    CountOut,
    CreateConversationIn,
    MemberIdsOut,
    MembershipOut,
    MembersIn,
    MuteIn,
    MuteOut,
    UpdateConversationIn,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def _to_out(conversation: Conversation) -> ConversationOut:
    """Translate the domain entity into the contract representation."""
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


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: CreateConversationIn,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> ConversationOut:
    """Create a conversation."""
    conversation = await conversations.create(
        conversation_type=payload.type,
        user_ids=payload.user_ids,
        name=payload.name,
        enabled=payload.enabled,
        component=payload.component,
        item_type=payload.item_type,
        item_id=payload.item_id,
        context_id=payload.context_id,
    )
    return _to_out(conversation)


@router.get("/individual", response_model=ConversationOut)
async def get_individual_conversation(
    conversations: ConversationServiceDep,
    user_ids: str = Query(description="Identificativi separati da virgola"),
    _: AuthDep = None,
) -> ConversationOut:
    """Return the individual conversation between the given users."""
    parsed = [int(uid) for uid in user_ids.split(",") if uid.strip()]
    return _to_out(await conversations.get_individual(parsed))


@router.get("/self", response_model=ConversationOut)
async def get_self_conversation(
    conversations: ConversationServiceDep,
    user_id: int = Query(),
    _: AuthDep = None,
) -> ConversationOut:
    """Return the self conversation of a user."""
    return _to_out(await conversations.get_self(user_id))


@router.get("/shared", response_model=ConversationListOut)
async def get_shared_conversations(
    conversations: ConversationServiceDep,
    user_id: int = Query(),
    other_user_id: int = Query(),
    offset: int = Query(default=0, ge=0),
    # The monolith calls with limit 1000.
    limit: int = Query(default=20, ge=0, le=1000),
    _: AuthDep = None,
) -> ConversationListOut:
    """Return the enabled conversations shared by two users."""
    found = await conversations.find_shared(user_id, other_user_id, offset, limit)
    return ConversationListOut(conversations=[_to_out(conversation) for conversation in found])


@router.get("/by-area", response_model=ConversationOut)
async def get_conversation_by_area(
    conversations: ConversationServiceDep,
    component: str = Query(),
    item_type: str = Query(),
    item_id: int = Query(),
    context_id: int = Query(),
    _: AuthDep = None,
) -> ConversationOut:
    """Return the conversation linked to an item of another component."""
    return _to_out(await conversations.get_by_area(component, item_type, item_id, context_id))


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: int,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> ConversationOut:
    """Return a conversation."""
    return _to_out(await conversations.get(conversation_id))


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: int,
    payload: UpdateConversationIn,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> ConversationOut:
    """Update the enabled state and the name of a conversation."""
    return _to_out(await conversations.update(conversation_id, payload.enabled, payload.name))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> Response:
    """Remove every datum owned by this context for the given conversation."""
    await conversations.delete_all_data(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/members", response_model=MemberIdsOut)
async def get_members(
    conversation_id: int,
    conversations: ConversationServiceDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=0, ge=0, le=1000),
    _: AuthDep = None,
) -> MemberIdsOut:
    """Identifiers of the members."""
    return MemberIdsOut(member_ids=await conversations.member_ids(conversation_id, offset, limit))


@router.post("/{conversation_id}/members", response_model=CountOut)
async def add_members(
    conversation_id: int,
    payload: MembersIn,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> CountOut:
    """Add members to a group conversation."""
    return CountOut(count=await conversations.add_members(conversation_id, payload.user_ids))


@router.delete("/{conversation_id}/members", response_model=CountOut)
async def remove_members(
    conversation_id: int,
    conversations: ConversationServiceDep,
    user_ids: str = Query(description="Identificativi separati da virgola"),
    _: AuthDep = None,
) -> CountOut:
    """Remove members from a group conversation."""
    parsed = [int(uid) for uid in user_ids.split(",") if uid.strip()]
    return CountOut(count=await conversations.remove_members(conversation_id, parsed))


@router.get("/{conversation_id}/members/count", response_model=CountOut)
async def count_members(
    conversation_id: int,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> CountOut:
    """Count the members of a conversation."""
    return CountOut(count=await conversations.count_members(conversation_id))


@router.get("/{conversation_id}/membership", response_model=MembershipOut)
async def is_member(
    conversation_id: int,
    conversations: ConversationServiceDep,
    user_id: int = Query(),
    _: AuthDep = None,
) -> MembershipOut:
    """Whether the user is a member of the conversation."""
    return MembershipOut(is_member=await conversations.is_member(conversation_id, user_id))


@router.post("/{conversation_id}/mute", response_model=MuteOut)
async def mute_conversation(
    conversation_id: int,
    payload: MuteIn,
    conversations: ConversationServiceDep,
    _: AuthDep = None,
) -> MuteOut:
    """Mute a conversation for a user."""
    await conversations.mute(conversation_id, payload.user_id)
    return MuteOut(muted=True)


@router.delete("/{conversation_id}/mute", response_model=MuteOut)
async def unmute_conversation(
    conversation_id: int,
    conversations: ConversationServiceDep,
    user_id: int = Query(),
    _: AuthDep = None,
) -> MuteOut:
    """Unmute a conversation for a user."""
    await conversations.unmute(conversation_id, user_id)
    return MuteOut(muted=False)


@router.get("/{conversation_id}/mute", response_model=MuteOut)
async def is_conversation_muted(
    conversation_id: int,
    conversations: ConversationServiceDep,
    user_id: int = Query(),
    _: AuthDep = None,
) -> MuteOut:
    """Whether a conversation is muted for a user."""
    return MuteOut(muted=await conversations.is_muted(conversation_id, user_id))
