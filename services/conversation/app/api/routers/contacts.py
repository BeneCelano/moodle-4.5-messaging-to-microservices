"""Routes of contacts, blocks and contact requests."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..deps import AuthDep, ContactServiceDep, CorrelationDep
from ..schemas import (
    BlockExistsOut,
    BlockIn,
    BlockOut,
    ContactExistsOut,
    ContactIn,
    ContactOut,
    ContactRequestExistsOut,
    ContactRequestIn,
    ContactRequestOut,
    ContactRequestsOut,
    CountOut,
    UserIdsOut,
)

router = APIRouter(prefix="/api/v1", tags=["contacts"])


@router.post("/contacts", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def add_contact(
    payload: ContactIn,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> ContactOut:
    """Create the contact relationship between two users."""
    contact = await contacts.add_contact(payload.user_id, payload.contact_id)
    return ContactOut(
        id=int(contact.id),
        user_id=contact.user_id,
        contact_id=contact.contact_id,
        time_created=contact.time_created,
    )


@router.get("/contacts", response_model=ContactOut)
async def get_contact(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    contact_id: int = Query(),
    _: AuthDep = None,
) -> ContactOut:
    """Return the contact relationship between two users."""
    contact = await contacts.get_contact(user_id, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found.")

    return ContactOut(
        id=int(contact.id),
        user_id=contact.user_id,
        contact_id=contact.contact_id,
        time_created=contact.time_created,
    )


@router.get("/contacts/exists", response_model=ContactExistsOut)
async def contact_exists(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    contact_id: int = Query(),
    _: AuthDep = None,
) -> ContactExistsOut:
    """Whether two users are contacts."""
    return ContactExistsOut(is_contact=await contacts.get_contact(user_id, contact_id) is not None)


@router.delete("/contacts", response_model=ContactOut | dict)
async def remove_contact(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    contact_id: int = Query(),
    _: AuthDep = None,
) -> ContactOut | dict:
    """Remove the contact relationship between two users."""
    contact = await contacts.remove_contact(user_id, contact_id)
    if contact is None:
        return {}

    return ContactOut(
        id=int(contact.id),
        user_id=contact.user_id,
        contact_id=contact.contact_id,
        time_created=contact.time_created,
    )


@router.get("/users/{user_id}/contacts/count", response_model=CountOut)
async def count_contacts(
    user_id: int,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> CountOut:
    """Count the contacts of a user."""
    return CountOut(count=await contacts.count_contacts(user_id))


@router.get("/users/{user_id}/contacts", response_model=UserIdsOut)
async def list_contacts(
    user_id: int,
    contacts: ContactServiceDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(0, ge=0),
    _: AuthDep = None,
) -> UserIdsOut:
    """List the users in contact with the given one, most recent first."""
    return UserIdsOut(user_ids=await contacts.list_contact_ids(user_id, offset, limit))


@router.post("/blocks", response_model=BlockOut, status_code=status.HTTP_201_CREATED)
async def block_user(
    payload: BlockIn,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> BlockOut:
    """Record the block of a user."""
    block = await contacts.block(payload.user_id, payload.blocked_user_id)
    return BlockOut(
        id=int(block.id),
        user_id=block.user_id,
        blocked_user_id=block.blocked_user_id,
        time_created=block.time_created,
    )


@router.delete("/blocks", response_model=BlockOut | dict)
async def unblock_user(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    blocked_user_id: int = Query(),
    _: AuthDep = None,
) -> BlockOut | dict:
    """Remove the block of a user."""
    block = await contacts.unblock(user_id, blocked_user_id)
    if block is None:
        return {}

    return BlockOut(
        id=int(block.id),
        user_id=block.user_id,
        blocked_user_id=block.blocked_user_id,
        time_created=block.time_created,
    )


@router.get("/blocks/exists", response_model=BlockExistsOut)
async def block_exists(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    blocked_user_id: int = Query(),
    _: AuthDep = None,
) -> BlockExistsOut:
    """Whether a user has blocked another."""
    return BlockExistsOut(is_blocked=await contacts.is_blocked(user_id, blocked_user_id))


@router.get("/users/{user_id}/blocks", response_model=UserIdsOut)
async def list_blocked_users(
    user_id: int,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> UserIdsOut:
    """List the users blocked by the given one."""
    return UserIdsOut(user_ids=await contacts.list_blocked_user_ids(user_id))


@router.post(
    "/contact-requests", response_model=ContactRequestOut, status_code=status.HTTP_201_CREATED
)
async def create_contact_request(
    payload: ContactRequestIn,
    contacts: ContactServiceDep,
    correlation_id: CorrelationDep = None,
    _: AuthDep = None,
) -> ContactRequestOut:
    """Create a contact request."""
    request = await contacts.create_request(payload.user_id, payload.requested_user_id, correlation_id)
    return ContactRequestOut(
        id=int(request.id),
        user_id=request.user_id,
        requested_user_id=request.requested_user_id,
        time_created=request.time_created,
    )


@router.post("/contact-requests/confirm", response_model=ContactOut | dict)
async def confirm_contact_request(
    payload: ContactRequestIn,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> ContactOut | dict:
    """Confirm a contact request."""
    contact = await contacts.confirm_request(payload.user_id, payload.requested_user_id)
    if contact is None:
        return {}

    return ContactOut(
        id=int(contact.id),
        user_id=contact.user_id,
        contact_id=contact.contact_id,
        time_created=contact.time_created,
    )


@router.post("/contact-requests/decline", status_code=status.HTTP_200_OK)
async def decline_contact_request(
    payload: ContactRequestIn,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> dict:
    """Decline a contact request."""
    declined = await contacts.decline_request(payload.user_id, payload.requested_user_id)
    return {"declined": declined}


@router.get("/contact-requests", response_model=ContactRequestsOut)
async def list_contact_requests(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    requested_user_id: int = Query(),
    _: AuthDep = None,
) -> ContactRequestsOut:
    """Return the contact requests between two users, in both directions."""
    found = await contacts.requests_between(user_id, requested_user_id)
    return ContactRequestsOut(
        requests=[
            ContactRequestOut(
                id=int(request.id),
                user_id=request.user_id,
                requested_user_id=request.requested_user_id,
                time_created=request.time_created,
            )
            for request in found
        ]
    )


@router.get("/contact-requests/exists", response_model=ContactRequestExistsOut)
async def contact_request_exists(
    contacts: ContactServiceDep,
    user_id: int = Query(),
    requested_user_id: int = Query(),
    _: AuthDep = None,
) -> ContactRequestExistsOut:
    """Whether a contact request exists between two users."""
    found = await contacts.requests_between(user_id, requested_user_id)
    return ContactRequestExistsOut(exists=bool(found))


@router.get("/users/{user_id}/contact-requests/received/count", response_model=CountOut)
async def count_received_requests(
    user_id: int,
    contacts: ContactServiceDep,
    _: AuthDep = None,
) -> CountOut:
    """Count the contact requests received by a user."""
    return CountOut(count=await contacts.count_received_requests(user_id))


@router.get("/users/{user_id}/contact-requests", response_model=UserIdsOut)
async def list_received_contact_requests(
    user_id: int,
    contacts: ContactServiceDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(0, ge=0),
    _: AuthDep = None,
) -> UserIdsOut:
    """List who asked the user for contact, excluding those the user blocked."""
    return UserIdsOut(
        user_ids=await contacts.list_received_request_user_ids(user_id, offset, limit)
    )
