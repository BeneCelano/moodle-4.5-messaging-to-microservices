"""Routes of the notification context."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ...domain.constants import SORT_ASC, SORT_DESC
from ...domain.entities import Notification
from ..deps import AuthDep, CorrelationDep, IdempotencyDep, NotificationServiceDep
from ..schemas import (
    CleanupIn,
    CleanupOut,
    CountOut,
    ErasureOut,
    ExistsOut,
    MarkAllReadOut,
    MarkReadOut,
    NotificationListOut,
    NotificationOut,
    SeedTemplatesIn,
    SeedTemplatesOut,
    SendNotificationIn,
    SendNotificationOut,
)


def _to_out(notification: Notification) -> NotificationOut:
    """Translate a domain notification into the response form."""
    return NotificationOut.model_validate(notification, from_attributes=True)


router = APIRouter(prefix="/api/v1", tags=["notifications"])


def _sort(value: str) -> str:
    """Normalise the sort direction, refusing what is not one."""
    normalised = value.upper()
    return normalised if normalised in (SORT_ASC, SORT_DESC) else SORT_DESC


# -- writes --------------------------------------------------------------------------------


@router.post(
    "/notifications", response_model=SendNotificationOut, status_code=status.HTTP_201_CREATED
)
async def send_notification(
    payload: SendNotificationIn,
    service: NotificationServiceDep,
    _: AuthDep,
    correlation_id: CorrelationDep = None,
    idempotency_key: IdempotencyDep = None,
) -> SendNotificationOut:
    """Write a notification and announce it."""
    outcome = await service.send(
        user_id_from=payload.user_id_from,
        user_id_to=payload.user_id_to,
        component=payload.component,
        event_type=payload.event_type,
        subject=payload.subject,
        full_message=payload.full_message,
        full_message_format=payload.full_message_format,
        full_message_html=payload.full_message_html,
        small_message=payload.small_message,
        context_url=payload.context_url,
        context_url_name=payload.context_url_name,
        custom_data=payload.custom_data,
        in_popup=payload.in_popup,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    return SendNotificationOut(**outcome)


@router.post("/notifications/{notification_id}/read", response_model=MarkReadOut)
async def mark_read(
    notification_id: int,
    service: NotificationServiceDep,
    _: AuthDep,
    time_read: int | None = Query(default=None, ge=0),
) -> MarkReadOut:
    """Mark a notification as read."""
    return MarkReadOut(**await service.mark_read(notification_id, time_read))


@router.post("/users/{user_id}/notifications/read-all", response_model=MarkAllReadOut)
async def mark_all_read(
    user_id: int,
    service: NotificationServiceDep,
    _: AuthDep,
    user_id_from: int | None = Query(default=None),
    time_created_to: int | None = Query(default=None, ge=0),
) -> MarkAllReadOut:
    """Mark all the unread notifications of a person as read."""
    return MarkAllReadOut(
        **await service.mark_all_read(
            user_id, user_id_from=user_id_from, time_created_to=time_created_to
        )
    )


@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: int, service: NotificationServiceDep, _: AuthDep
) -> None:
    """Remove a notification."""
    await service.delete(notification_id)


@router.post("/maintenance/cleanup", response_model=CleanupOut)
async def cleanup(payload: CleanupIn, service: NotificationServiceDep, _: AuthDep) -> CleanupOut:
    """Delete the expired notifications."""
    by_age = (
        await service.delete_created_before(payload.created_before)
        if payload.created_before > 0
        else 0
    )
    by_read = (
        await service.delete_read_before(payload.read_before) if payload.read_before > 0 else 0
    )
    return CleanupOut(deleted_by_age=by_age, deleted_by_read=by_read)


# -- reads ---------------------------------------------------------------------------------


@router.get("/notifications", response_model=NotificationListOut)
async def list_notifications_broadly(
    service: NotificationServiceDep,
    _: AuthDep,
    user_id_to: int | None = Query(default=None),
    user_id_from: int | None = Query(default=None),
    read: bool | None = Query(default=None),
    sort: str = Query(default=SORT_DESC),
    limit_from: int = Query(default=0, ge=0),
    limit_num: int = Query(default=0, ge=0),
) -> NotificationListOut:
    """List the notifications of a person, received or sent."""
    if user_id_to is None and user_id_from is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Indicare almeno user_id_to o user_id_from.",
        )

    notifications = await service.list_for_user(
        user_id_to,
        user_id_from=user_id_from,
        read=read,
        sort=_sort(sort),
        limit_from=limit_from,
        limit_num=limit_num,
    )
    return NotificationListOut(notifications=[_to_out(n) for n in notifications])


@router.get("/notifications/{notification_id}", response_model=NotificationOut)
async def get_notification(
    notification_id: int, service: NotificationServiceDep, _: AuthDep
) -> NotificationOut:
    """Return a notification."""
    notification = await service.get(notification_id)
    return _to_out(notification)


@router.get("/users/{user_id}/notifications", response_model=NotificationListOut)
async def list_notifications(
    user_id: int,
    service: NotificationServiceDep,
    _: AuthDep,
    user_id_from: int | None = Query(default=None),
    read: bool | None = Query(default=None),
    sort: str = Query(default=SORT_DESC),
    limit_from: int = Query(default=0, ge=0),
    limit_num: int = Query(default=0, ge=0),
) -> NotificationListOut:
    """List the notifications received by a person."""
    notifications = await service.list_for_user(
        user_id,
        user_id_from=user_id_from,
        read=read,
        sort=_sort(sort),
        limit_from=limit_from,
        limit_num=limit_num,
    )
    return NotificationListOut(notifications=[_to_out(n) for n in notifications])


@router.get("/users/{user_id}/notifications/unread-count", response_model=CountOut)
async def count_unread(
    user_id: int,
    service: NotificationServiceDep,
    _: AuthDep,
    user_id_from: int | None = Query(default=None),
) -> CountOut:
    """Count the unread notifications received by a person."""
    return CountOut(count=await service.count_unread(user_id, user_id_from=user_id_from))


@router.get("/users/{user_id}/notifications/popup", response_model=NotificationListOut)
async def list_popup(
    user_id: int,
    service: NotificationServiceDep,
    _: AuthDep,
    sort: str = Query(default=SORT_DESC),
    limit_from: int = Query(default=0, ge=0),
    limit_num: int = Query(default=0, ge=0),
) -> NotificationListOut:
    """List the notifications shown in the popup."""
    notifications = await service.list_in_popup(
        user_id, sort=_sort(sort), limit_from=limit_from, limit_num=limit_num
    )
    return NotificationListOut(notifications=[_to_out(n) for n in notifications])


@router.get("/users/{user_id}/notifications/popup/unread-count", response_model=CountOut)
async def count_unread_popup(
    user_id: int, service: NotificationServiceDep, _: AuthDep
) -> CountOut:
    """Count the unread notifications shown in the popup."""
    return CountOut(count=await service.count_unread_in_popup(user_id))


@router.get("/users/{user_id}/notifications/exists", response_model=ExistsOut)
async def exists_with_custom_data(
    user_id: int,
    service: NotificationServiceDep,
    _: AuthDep,
    custom_data: str = Query(min_length=1),
    component: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
) -> ExistsOut:
    """Whether that person already received a notification with that custom data."""
    return ExistsOut(
        exists=await service.exists_with_custom_data(
            user_id, custom_data, component=component, event_type=event_type
        )
    )


# -- personal data -------------------------------------------------------------------------
#
# These three combine the two ends with OR (what the site keeps about a person); the other reads
# with AND (what a person sees).


@router.get("/users/{user_id}/personal-data/exists", response_model=ExistsOut)
async def has_personal_data(
    user_id: int, service: NotificationServiceDep, _: AuthDep
) -> ExistsOut:
    """Whether the site keeps notifications about this person."""
    return ExistsOut(exists=await service.has_personal_data(user_id))


@router.get("/users/{user_id}/personal-data/notifications", response_model=NotificationListOut)
async def export_personal_data(
    user_id: int, service: NotificationServiceDep, _: AuthDep
) -> NotificationListOut:
    """Return every notification about this person, oldest first."""
    notifications = await service.export_personal_data(user_id)
    return NotificationListOut(notifications=[_to_out(n) for n in notifications])


@router.delete("/users/{user_id}/personal-data/notifications", response_model=ErasureOut)
async def erase_personal_data(
    user_id: int, service: NotificationServiceDep, _: AuthDep
) -> ErasureOut:
    """Remove every notification about this person and return how many, so that the erasure can be
    accounted for.
    """
    return ErasureOut(deleted=await service.erase_personal_data(user_id))


# -- installation --------------------------------------------------------------------------


@router.post("/templates", response_model=SeedTemplatesOut)
async def seed_templates(
    payload: SeedTemplatesIn, service: NotificationServiceDep, _: AuthDep
) -> SeedTemplatesOut:
    """Register the templates with which the service composes the notifications it
    produces itself.
    """
    return SeedTemplatesOut(
        **await service.seed_templates([t.model_dump() for t in payload.templates])
    )
